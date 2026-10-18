/* -*- c-set-style: "K&R"; c-basic-offset: 8 -*-
 *
 * This file is part of PRoot.
 *
 * Copyright (C) 2015 STMicroelectronics
 *
 * This program is free software; you can redistribute it and/or
 * modify it under the terms of the GNU General Public License as
 * published by the Free Software Foundation; either version 2 of the
 * License, or (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful, but
 * WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
 * General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program; if not, write to the Free Software
 * Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA
 * 02110-1301 USA.
 */

#include <stdbool.h>     /* bool, true, false,  */

#define NO_LIBC_HEADER
#include "loader/script.h"
#include "compat.h"
#include "arch.h"

#if defined(ARCH_X86_64)
#    include "loader/assembly-x86_64.h"
#elif defined(ARCH_ARM_EABI)
#    include "loader/assembly-arm.h"
#elif defined(ARCH_X86)
#    include "loader/assembly-x86.h"
#elif defined(ARCH_ARM64)
#    include "loader/assembly-arm64.h"
#else
#    error "Unsupported architecture"
#endif

#if !defined(MMAP_OFFSET_SHIFT)
#    define MMAP_OFFSET_SHIFT 0
#endif

#define FATAL() do {						\
		SYSCALL(EXIT, 1, 182);				\
		__builtin_unreachable();			\
	} while (0)

#define unlikely(expr) __builtin_expect(!!(expr), 0)

#define HOST_MAX_PAGE_SIZE 0x4000ULL /* 16384 bytes (covers both 4KB and 16KB kernels) */
#define HOST_PAGE_MASK     (~(HOST_MAX_PAGE_SIZE - 1ULL))
#ifndef EINTR
#define EINTR 4
#endif

static inline void freestanding_zero(void *dst, word_t count)
{
	volatile byte_t *p = (volatile byte_t *)dst;
	while (count--)
		*p++ = 0;
}

/**
 * Return the address of the last path component of @string_.  Note
 * that @string_ is not modified.
 */
static inline word_t basename(word_t string_)
{
	byte_t *string = (byte_t *) string_;
	byte_t *cursor;

	for (cursor = string; *cursor != 0; cursor++)
		;

	for (; *cursor != (byte_t) '/' && cursor > string; cursor--)
		;

	if (cursor != string)
		cursor++;

	return (word_t) cursor;
}

/**
 * Interpret the load script pointed to by @cursor.
 */
void _start(void *cursor)
{
	bool traced = false;
	bool reset_at_base = true;
	word_t at_base = 0;

	word_t fd = -1;
	word_t status;
	word_t last_mapped_page_end = 0;
	word_t last_mapped_page_prot = 0;

	while(1) {
		LoadStatement *stmt = cursor;

		switch (stmt->action) {
		case LOAD_ACTION_OPEN_NEXT:
			status = SYSCALL(CLOSE, 1, fd);
			if (unlikely((int) status < 0))
				FATAL();
			/* Fall through.  */

		case LOAD_ACTION_OPEN:
#if defined(OPEN)
			fd = SYSCALL(OPEN, 3, stmt->open.string_address, O_RDONLY, 0);
#else
			fd = SYSCALL(OPENAT, 4, AT_FDCWD, stmt->open.string_address, O_RDONLY, 0);
#endif
			if (unlikely((int) fd < 0))
				FATAL();

			reset_at_base = true;
			last_mapped_page_end = 0;
			last_mapped_page_prot = 0;

			cursor += LOAD_STATEMENT_SIZE(*stmt, open);
			break;

		case LOAD_ACTION_MMAP_FILE:
		case LOAD_ACTION_MMAP_ANON: {
			bool anonymous = (stmt->action == LOAD_ACTION_MMAP_ANON);
			word_t seg_start = stmt->mmap.addr;
			word_t seg_end   = stmt->mmap.addr + stmt->mmap.length;
			word_t map_start = seg_start & HOST_PAGE_MASK;
			word_t map_end   = (seg_end + HOST_MAX_PAGE_SIZE - 1ULL) & HOST_PAGE_MASK;

			word_t alloc_start = map_start;
			word_t shared_page = 0;

			/*
			 * If a 4KB-aligned ELF has two adjacent PT_LOAD segments sharing the
			 * same 16KB boundary page, do NOT re-mmap that shared 16KB page with
			 * MAP_FIXED (which would clobber the previous segment's bytes!).
			 * Instead, make the shared boundary page writable before pread64.
			 */
			if (alloc_start < last_mapped_page_end) {
				shared_page = alloc_start;
				status = SYSCALL(MPROTECT, 3, shared_page, HOST_MAX_PAGE_SIZE,
						 PROT_READ | PROT_WRITE | (last_mapped_page_prot & PROT_EXEC));
				if ((long)status < 0)
					FATAL();
				alloc_start = last_mapped_page_end;
			}

			/* 1. Allocate new 16KB-aligned anonymous RW pages for the rest of the segment */
			if (map_end > alloc_start) {
				status = SYSCALL(MMAP, 6, alloc_start, map_end - alloc_start,
						 PROT_READ | PROT_WRITE,
						 MAP_PRIVATE | MAP_FIXED | MAP_ANONYMOUS, (word_t)-1, 0);
				if ((void *)status == MAP_FAILED || status != alloc_start)
					FATAL();
			}

			/* 2. Populate exact segment bytes (even at 4KB-aligned seg_start) via pread64 */
			if (!anonymous && stmt->mmap.length > stmt->mmap.clear_length) {
				word_t filesz = stmt->mmap.length - stmt->mmap.clear_length;
				word_t total  = 0;
				while (total < filesz) {
					long n = (long)SYSCALL(PREAD64, 4, fd,
								seg_start + total,
								filesz - total,
								stmt->mmap.offset + total);
					if (n < 0) {
						if (n == -EINTR) continue;
						FATAL();
					}
					if (n == 0) break; /* EOF reached */
					total += (word_t)n;
				}
			}

			/* 3. Zero BSS tail explicitly (essential if sharing a boundary page) */
			if (stmt->mmap.clear_length > 0) {
				word_t bss_start = seg_end - stmt->mmap.clear_length;
				freestanding_zero((void *)bss_start, stmt->mmap.clear_length);
			}

			/* 4. Apply final segment protections (RX, R, or RW) at 16KB page granularity */
			if (shared_page != 0) {
				word_t merged_prot = last_mapped_page_prot | stmt->mmap.prot;
				status = SYSCALL(MPROTECT, 3, shared_page, HOST_MAX_PAGE_SIZE, merged_prot);
				if ((long)status < 0)
					FATAL();
			}
			if (map_end > alloc_start && stmt->mmap.prot != (PROT_READ | PROT_WRITE)) {
				status = SYSCALL(MPROTECT, 3, alloc_start, map_end - alloc_start, stmt->mmap.prot);
				if ((long)status < 0)
					FATAL();
			}

			if (map_end > last_mapped_page_end) {
				last_mapped_page_end  = map_end;
				last_mapped_page_prot = stmt->mmap.prot;
			} else if (shared_page != 0) {
				last_mapped_page_prot |= stmt->mmap.prot;
			}

			if (!anonymous && reset_at_base) {
				at_base = stmt->mmap.addr;
				reset_at_base = false;
			}

			cursor += LOAD_STATEMENT_SIZE(*stmt, mmap);
			break;
		}

		case LOAD_ACTION_MAKE_STACK_EXEC:
			SYSCALL(MPROTECT, 3,
				stmt->make_stack_exec.start, 1,
				PROT_READ | PROT_WRITE | PROT_EXEC | PROT_GROWSDOWN);

			cursor += LOAD_STATEMENT_SIZE(*stmt, make_stack_exec);
			break;

		case LOAD_ACTION_START_TRACED:
			traced = true;
			/* Fall through.  */

		case LOAD_ACTION_START: {
			word_t *cursor2 = (word_t *) stmt->start.stack_pointer;
			const word_t argc = cursor2[0];
			const word_t at_execfn = cursor2[1];
			word_t name;

			status = SYSCALL(CLOSE, 1, fd);
			if (unlikely((int) status < 0))
				FATAL();

			/* Right after execve, the stack content is as follow:
			 *
			 *   +------+--------+--------+--------+
			 *   | argc | argv[] | envp[] | auxv[] |
			 *   +------+--------+--------+--------+
			 */

			/* Skip argv[].  */
			cursor2 += argc + 1;

			/* Skip envp[].  */
			do cursor2++; while (cursor2[0] != 0);
			cursor2++;

			/* Adjust auxv[].  */
			do {
				switch (cursor2[0]) {
				case AT_PHDR:
					cursor2[1] = stmt->start.at_phdr;
					break;

				case AT_PHENT:
					cursor2[1] = stmt->start.at_phent;
					break;

				case AT_PHNUM:
					cursor2[1] = stmt->start.at_phnum;
					break;

				case AT_ENTRY:
					cursor2[1] = stmt->start.at_entry;
					break;

				case AT_BASE:
					cursor2[1] = at_base;
					break;

				case AT_EXECFN:
					/* stmt->start.at_execfn can't be used for now since it is
					 * currently stored in a location that will be scratched
					 * by the process (below the final stack pointer).  */
					cursor2[1] = at_execfn;
					break;

				default:
					break;
				}
				cursor2 += 2;
			} while (cursor2[0] != AT_NULL);

			/* Note that only 2 arguments are actually necessary... */
			name = basename(stmt->start.at_execfn);
			SYSCALL(PRCTL, 3, PR_SET_NAME, name, 0);

			if (unlikely(traced))
				SYSCALL(EXECVE, 6, 1,
					stmt->start.stack_pointer,
					stmt->start.entry_point, 2, 3, 4);
			else
				BRANCH(stmt->start.stack_pointer, stmt->start.entry_point);
			FATAL();
		}

		default:
			FATAL();
		}
	}

	FATAL();
}
