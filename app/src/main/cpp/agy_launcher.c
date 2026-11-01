#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

#define GLIBC_LD   "/data/data/ai.agy.app/files/usr/glibc/lib/ld-linux-aarch64.so.1"
#define GLIBC_LIB  "/data/data/ai.agy.app/files/usr/glibc/lib"
#define AGY_ENGINE "/data/data/ai.agy.app/files/usr/opt/agy/agy"

int main(int argc, char *argv[]) {
    if (access(AGY_ENGINE, F_OK) != 0) {
        fprintf(stderr,
            "Antigravity CLI engine is not installed.\n"
            "Please open Antigravity Studio -> Settings -> Software Updates to install it.\n");
        return 127;
    }

    if (access(GLIBC_LD, F_OK) != 0) {
        fprintf(stderr, "Error: Glibc dynamic linker not found at %s\n", GLIBC_LD);
        return 127;
    }

    /* Allocate argument array: 4 prefixed args + (argc - 1) passed args + 1 NULL terminator */
    char **new_argv = (char **)calloc((size_t)argc + 4, sizeof(char *));
    if (!new_argv) {
        perror("calloc failed");
        return 127;
    }

    new_argv[0] = "ld-linux-aarch64.so.1";
    new_argv[1] = "--library-path";
    new_argv[2] = GLIBC_LIB;
    new_argv[3] = AGY_ENGINE;

    for (int i = 1; i < argc; i++) {
        new_argv[i + 3] = argv[i];
    }
    new_argv[argc + 3] = NULL;

    execv(GLIBC_LD, new_argv);

    perror("execv failed to launch Antigravity CLI engine");
    free(new_argv);
    return 127;
}
