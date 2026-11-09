#!/usr/bin/env python3
"""
Test suite for Spec 06:
Standardized Stdio Binary Provisioning & Dual-Mode OAuth 2.1 HTTP MCP Architecture.
"""

import os
import sys
import json
import io
import re
import zipfile
import tarfile
import struct
import tempfile
import asyncio
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

# Add agy-ui to sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agy-ui"))

import server


class TestMcpBinaryProvisioner(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _create_mock_arm64_elf(self, p_align=16384):
        e_ident = b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 8
        e_type = 2  # ET_EXEC
        e_machine = 0xB7  # EM_AARCH64 (183)
        e_version = 1
        e_entry = 0x400000
        e_phoff = 64
        e_shoff = 0
        e_flags = 0
        e_ehsize = 64
        e_phentsize = 56
        e_phnum = 1
        e_shentsize = 64
        e_shnum = 0
        e_shstrndx = 0

        elf_hdr = struct.pack(
            "<16sHHIQQQIHHHHHH",
            e_ident,
            e_type,
            e_machine,
            e_version,
            e_entry,
            e_phoff,
            e_shoff,
            e_flags,
            e_ehsize,
            e_phentsize,
            e_phnum,
            e_shentsize,
            e_shnum,
            e_shstrndx,
        )

        phdr = struct.pack(
            "<IIQQQQQQ",
            1,
            5,
            0,
            0x400000,
            0x400000,
            120,
            120,
            p_align,
        )

        return elf_hdr + phdr

    def test_validate_elf_arm64(self):
        elf_bytes = self._create_mock_arm64_elf(16384)
        elf_path = Path(self.temp_dir.name) / "test_binary"
        with open(elf_path, "wb") as f:
            f.write(elf_bytes)

        server.McpBinaryProvisioner.validate_elf_binary(elf_path)

        # Test invalid architecture (x86_64 = 0x3E)
        bad_arch_elf = bytearray(elf_bytes)
        bad_arch_elf[18:20] = struct.pack("<H", 0x3E)
        with open(elf_path, "wb") as f:
            f.write(bad_arch_elf)

        with self.assertRaises(ValueError) as ctx:
            server.McpBinaryProvisioner.validate_elf_binary(elf_path)
        self.assertIn("Incompatible architecture: x86_64", str(ctx.exception))

        # Test non-ELF
        with open(elf_path, "wb") as f:
            f.write(b"MZ" + b"\x00" * 100)
        with self.assertRaises(ValueError) as ctx:
            server.McpBinaryProvisioner.validate_elf_binary(elf_path)
        self.assertIn("Windows executable detected", str(ctx.exception))

    def test_provision_stdio_binary_zip_extraction(self):
        async def _test():
            elf_bytes = self._create_mock_arm64_elf(16384)
            zip_buf = io.BytesIO()
            with zipfile.ZipFile(zip_buf, "w") as zf:
                zf.writestr("my-server", elf_bytes)
                zf.writestr("README.md", b"# Readme")
                zf.writestr("../evil.sh", b"evil script")

            zip_content = zip_buf.getvalue()

            mock_resp = AsyncMock()
            mock_resp.status_code = 200
            mock_resp.headers = {"Content-Length": str(len(zip_content))}
            mock_resp.aiter_bytes = MagicMock(return_value=_async_iter([zip_content]))

            stream_cm = MagicMock()
            stream_cm.__aenter__ = AsyncMock(return_value=mock_resp)
            stream_cm.__aexit__ = AsyncMock(return_value=None)

            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.stream = MagicMock(return_value=stream_cm)

            with patch("httpx.AsyncClient", return_value=mock_client), \
                 patch.object(server, "MCP_BIN_DIR", Path(self.temp_dir.name) / "mcp-bin"):
                target_path, checksum = await server.McpBinaryProvisioner.provision_stdio_binary(
                    server_name="test-server",
                    command="my-server",
                    download_url="https://example.com/download.zip"
                )
                self.assertTrue(target_path.exists())
                self.assertEqual(target_path.name, "my-server")
                self.assertEqual(len(checksum), 64)
                self.assertFalse((Path(self.temp_dir.name) / "evil.sh").exists())

        asyncio.run(_test())

    def test_provision_stdio_binary_tar_extraction(self):
        async def _test():
            elf_bytes = self._create_mock_arm64_elf(16384)
            tar_buf = io.BytesIO()
            with tarfile.open(fileobj=tar_buf, mode="w:gz") as tf:
                ti = tarfile.TarInfo(name="bin/tar-server")
                ti.size = len(elf_bytes)
                ti.type = tarfile.REGTYPE
                tf.addfile(ti, io.BytesIO(elf_bytes))

                # Add symlink (must be skipped per Zero-Symlink Policy)
                ti_link = tarfile.TarInfo(name="bin/evil-link")
                ti_link.type = tarfile.SYMTYPE
                ti_link.linkname = "/etc/passwd"
                tf.addfile(ti_link)

            tar_content = tar_buf.getvalue()

            mock_resp = AsyncMock()
            mock_resp.status_code = 200
            mock_resp.headers = {"Content-Length": str(len(tar_content))}
            mock_resp.aiter_bytes = MagicMock(return_value=_async_iter([tar_content]))

            stream_cm = MagicMock()
            stream_cm.__aenter__ = AsyncMock(return_value=mock_resp)
            stream_cm.__aexit__ = AsyncMock(return_value=None)

            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.stream = MagicMock(return_value=stream_cm)

            with patch("httpx.AsyncClient", return_value=mock_client), \
                 patch.object(server, "MCP_BIN_DIR", Path(self.temp_dir.name) / "mcp-bin"):
                target_path, checksum = await server.McpBinaryProvisioner.provision_stdio_binary(
                    server_name="tar-test",
                    command="tar-server",
                    download_url="https://example.com/download.tar.gz"
                )
                self.assertTrue(target_path.exists())
                self.assertEqual(target_path.name, "tar-server")
                self.assertFalse((target_path.parent / "evil-link").exists())

        asyncio.run(_test())


async def _async_iter(items):
    for item in items:
        yield item


class TestMcpOAuth2Manager(unittest.TestCase):
    def test_rfc9728_discovery(self):
        async def _test():
            # Mock 401 response with WWW-Authenticate resource_metadata
            mock_probe = MagicMock()
            mock_probe.status_code = 401
            mock_probe.headers = {"WWW-Authenticate": 'Bearer realm="mcp", resource_metadata="https://auth.example.com/.well-known/oauth-protected-resource"'}

            mock_meta = MagicMock()
            mock_meta.status_code = 200
            mock_meta.json.return_value = {
                "authorization_endpoint": "https://auth.example.com/oauth/authorize",
                "token_endpoint": "https://auth.example.com/oauth/token",
                "registration_endpoint": "https://auth.example.com/oauth/register"
            }

            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(side_effect=[mock_probe, mock_meta])

            with patch("httpx.AsyncClient", return_value=mock_client):
                endpoints = await server.McpOAuth2Manager.discover_oauth_endpoints("https://mcp.example.com/api")
                self.assertEqual(endpoints["authorization_endpoint"], "https://auth.example.com/oauth/authorize")
                self.assertEqual(endpoints["token_endpoint"], "https://auth.example.com/oauth/token")
                self.assertEqual(endpoints["registration_endpoint"], "https://auth.example.com/oauth/register")

        asyncio.run(_test())

    def test_initiate_oauth_flow_pkce(self):
        async def _test():
            mock_vault = {
                "my-oauth-server": {
                    "upstreamUrl": "https://mcp.example.com",
                    "client_id": "client-12345",
                    "scopes": "read write"
                }
            }

            with patch.object(server, "load_mcp_vault", return_value=mock_vault), \
                 patch.object(server.McpOAuth2Manager, "discover_oauth_endpoints", return_value={
                     "authorization_endpoint": "https://auth.example.com/authorize",
                     "token_endpoint": "https://auth.example.com/token"
                 }):
                auth_url = await server.McpOAuth2Manager.initiate_oauth_flow(
                    "my-oauth-server", "http://127.0.0.1:8765/oauth/callback"
                )
                self.assertIn("https://auth.example.com/authorize?", auth_url)
                self.assertIn("response_type=code", auth_url)
                self.assertIn("client_id=client-12345", auth_url)
                self.assertIn("code_challenge_method=S256", auth_url)
                self.assertIn("scope=read+write", auth_url)

                self.assertEqual(len(server._pending_oauth_sessions), 1)
                state = list(server._pending_oauth_sessions.keys())[0]
                session = server._pending_oauth_sessions[state]
                self.assertEqual(session["server_name"], "my-oauth-server")
                self.assertEqual(len(session["code_verifier"]), 86)
                server._pending_oauth_sessions.clear()

        asyncio.run(_test())


class TestBackupExportImportSpec06(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.mcp_config_path = Path(self.temp_dir.name) / "mcp_config.json"
        self.orig_mcp_config = server.MCP_CONFIG_FILE
        server.MCP_CONFIG_FILE = self.mcp_config_path

    def tearDown(self):
        server.MCP_CONFIG_FILE = self.orig_mcp_config
        self.temp_dir.cleanup()

    def test_export_omits_oauth_tokens_preserves_config(self):
        async def _test():
            mock_config = {
                "mcpServers": {
                    "local-git": {
                        "command": "git-mcp",
                        "args": ["serve"],
                        "downloadUrl": "https://example.com/git-mcp.tar.gz",
                        "checksum": "sha256:12345"
                    },
                    "remote-cloud": {
                        "url": "https://cloud.example.com/mcp",
                        "auth_mode": "oauth2",
                        "client_id": "cid-999",
                        "scopes": ["tools", "read"]
                    }
                }
            }
            with open(self.mcp_config_path, "w") as f:
                json.dump(mock_config, f)

            mock_vault = {
                "remote-cloud": {
                    "transport": "http",
                    "upstreamUrl": "https://cloud.example.com/mcp",
                    "auth_mode": "oauth2",
                    "client_id": "cid-999",
                    "scopes": "tools read",
                    "enabled": True,
                },
                "local-git": {
                    "transport": "stdio",
                    "command": "git-mcp",
                    "args": ["serve"],
                    "downloadUrl": "https://example.com/git-mcp.tar.gz",
                    "checksum": "sha256:12345",
                    "enabled": True,
                }
            }

            with patch.object(server, "load_mcp_vault", return_value=mock_vault), \
                 patch.object(server, "load_api_keys_store", return_value={"keys": {"GITHUB_TOKEN": {"value": "ghp_abc"}}}):
                fname, enc_bundle, dpath = await server.generate_config_export_bundle(
                    categories=["api_keys", "mcps"],
                    password="TestPassword123!"
                )

                # Decrypt the bundle to inspect inner markdown
                decrypted = server.decrypt_v2_bundle(enc_bundle, "TestPassword123!")

                # Verify API keys
                self.assertIn("GITHUB_TOKEN", decrypted)
                self.assertNotIn("__MCP_OAUTH_", decrypted)

                # Extract the MCP json section
                mcp_match = re.search(r"## Section: MCP Servers[\s\S]*?```json\s*([\s\S]*?)\s*```", decrypted)
                self.assertIsNotNone(mcp_match)
                export_mcps = json.loads(mcp_match.group(1))

                self.assertIn("local-git", export_mcps)
                self.assertEqual(export_mcps["local-git"]["downloadUrl"], "https://example.com/git-mcp.tar.gz")
                self.assertEqual(export_mcps["local-git"]["checksum"], "sha256:12345")

                self.assertIn("remote-cloud", export_mcps)
                self.assertEqual(export_mcps["remote-cloud"]["auth_mode"], "oauth2")
                self.assertEqual(export_mcps["remote-cloud"]["client_id"], "cid-999")
                self.assertNotIn("access_token", export_mcps["remote-cloud"])
                self.assertNotIn("refresh_token", export_mcps["remote-cloud"])
                self.assertNotIn("headers", export_mcps["remote-cloud"])

        asyncio.run(_test())

    def test_export_and_import_oauth_client_secret(self):
        async def _test():
            # 1. Test Export with client_secret
            mock_vault = {
                "github-oauth": {
                    "transport": "http",
                    "upstreamUrl": "https://api.githubcopilot.com/mcp/",
                    "auth_mode": "oauth2",
                    "client_id": "Iv1.myclientid",
                    "client_secret": "my-secret-token-xyz",
                    "scopes": "repo read:user",
                    "enabled": True,
                }
            }

            with patch.object(server, "load_mcp_vault", return_value=mock_vault), \
                 patch.object(server, "load_api_keys_store", return_value={"keys": {}}):
                fname, enc_bundle, dpath = await server.generate_config_export_bundle(
                    categories=["mcps"],
                    password="SecretPassword123!"
                )
                decrypted = server.decrypt_v2_bundle(enc_bundle, "SecretPassword123!")
                mcp_match = re.search(r"## Section: MCP Servers[\s\S]*?```json\s*([\s\S]*?)\s*```", decrypted)
                self.assertIsNotNone(mcp_match)
                export_mcps = json.loads(mcp_match.group(1))
                self.assertIn("github-oauth", export_mcps)
                self.assertEqual(export_mcps["github-oauth"]["client_secret"], "my-secret-token-xyz")
                self.assertEqual(export_mcps["github-oauth"]["client_id"], "Iv1.myclientid")

            # 2. Test Import with client_secret
            import hashlib
            body_text = (
                "## Section: MCP Servers\n\n"
                "```json\n"
                "{\n"
                '  "github-oauth": {\n'
                '    "serverUrl": "https://api.githubcopilot.com/mcp/",\n'
                '    "auth_mode": "oauth2",\n'
                '    "client_id": "Iv1.myclientid",\n'
                '    "client_secret": "my-secret-token-xyz",\n'
                '    "scopes": "repo read:user"\n'
                "  }\n"
                "}\n"
                "```\n"
            )
            chash = hashlib.sha256(body_text.strip().encode("utf-8")).hexdigest().lower()
            bundle_body = (
                "<!-- ANTIGRAVITY_STUDIO_EXPORT_V1 -->\n"
                "---\n"
                "generator: Antigravity Studio\n"
                "export_version: '1.0'\n"
                f'checksum: "sha256:{chash}"\n'
                "---\n\n"
                f"{body_text}"
            )

            imported_vault = {}
            with patch.object(server, "load_mcp_vault", return_value=imported_vault), \
                 patch.object(server, "save_mcp_vault"):
                summary = await server.restore_configurations_bundle(bundle_body)
                self.assertIn("github-oauth", summary["mcps"]["restored"])
                self.assertIn("github-oauth", imported_vault)
                self.assertEqual(imported_vault["github-oauth"]["client_secret"], "my-secret-token-xyz")
                self.assertEqual(imported_vault["github-oauth"]["client_id"], "Iv1.myclientid")
                self.assertFalse(imported_vault["github-oauth"]["authenticated"])

        asyncio.run(_test())

    def test_import_restores_stdio_executes_provisioning_and_sets_executable(self):
        async def _test():
            import hashlib
            body_text = (
                "## Section: MCP Servers\n\n"
                "```json\n"
                "{\n"
                '  "github-local": {\n'
                '    "command": "github-mcp-server",\n'
                '    "args": ["stdio"],\n'
                '    "downloadUrl": "https://example.com/github-mcp-server_linux_arm64.tar.gz",\n'
                '    "checksum": "34d5a268f65cd1e9b7107fa976f3571ccd575f9df00cec489fec79a039923a22",\n'
                '    "env": {"TOKEN": "$GH_TOKEN"}\n'
                "  }\n"
                "}\n"
                "```\n"
            )
            chash = hashlib.sha256(body_text.strip().encode("utf-8")).hexdigest().lower()
            bundle_body = (
                "<!-- ANTIGRAVITY_STUDIO_EXPORT_V1 -->\n"
                "---\n"
                "generator: Antigravity Studio\n"
                "export_version: '1.0'\n"
                f'checksum: "sha256:{chash}"\n'
                "---\n\n"
                f"{body_text}"
            )

            mock_staged_bin = Path(self.temp_dir.name) / "github-mcp-server"
            mock_staged_bin.touch(mode=0o755)

            imported_vault = {}
            mock_proc = AsyncMock()
            mock_proc.returncode = 0
            mock_proc.communicate = AsyncMock(return_value=(b"Server added", b""))

            with patch.object(server, "load_mcp_vault", return_value=imported_vault), \
                 patch.object(server, "save_mcp_vault"), \
                 patch.object(server.McpBinaryProvisioner, "provision_stdio_binary", AsyncMock(return_value=(mock_staged_bin, "34d5a268f65cd1e9b7107fa976f3571ccd575f9df00cec489fec79a039923a22"))), \
                 patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:

                summary = await server.restore_configurations_bundle(bundle_body)
                self.assertIn("github-local", summary["mcps"]["restored"])

                # 1. Verify vault was populated with target_bin and provisioned=True
                self.assertIn("github-local", imported_vault)
                v_entry = imported_vault["github-local"]
                self.assertEqual(v_entry["target_bin"], str(mock_staged_bin))
                self.assertTrue(v_entry["provisioned"])
                self.assertEqual(v_entry["args"], ["stdio"])

                # 2. Verify mcp_config.json command was updated to agy-sh
                self.assertTrue(self.mcp_config_path.exists())
                saved_cfg = json.loads(self.mcp_config_path.read_text(encoding="utf-8"))
                srv_cfg = saved_cfg["mcpServers"]["github-local"]
                self.assertIn("agy-sh", srv_cfg["command"])
                self.assertEqual(srv_cfg["args"], [str(mock_staged_bin), "stdio"])

                # 3. Verify agy mcp add CLI command was executed on import
                mock_exec.assert_called()
                called_args = mock_exec.call_args[0]
                self.assertIn("mcp", called_args)
                self.assertIn("add", called_args)
                self.assertIn("github-local", called_args)

        asyncio.run(_test())

    def test_import_restores_oauth_with_clean_reauth(self):
        async def _test():
            import hashlib
            body_text = (
                "## Section: MCP Servers\n\n"
                "```json\n"
                "{\n"
                '  "remote-oauth": {\n'
                '    "serverUrl": "https://api.example.com/mcp",\n'
                '    "auth_mode": "oauth2",\n'
                '    "client_id": "my-client",\n'
                '    "scopes": "tools"\n'
                "  }\n"
                "}\n"
                "```\n"
            )
            chash = hashlib.sha256(body_text.strip().encode("utf-8")).hexdigest().lower()
            bundle_body = (
                "<!-- ANTIGRAVITY_STUDIO_EXPORT_V1 -->\n"
                "---\n"
                "generator: Antigravity Studio\n"
                "export_version: '1.0'\n"
                f'checksum: "sha256:{chash}"\n'
                "---\n\n"
                f"{body_text}"
            )

            mock_vault = {}
            with patch.object(server, "load_mcp_vault", return_value=mock_vault), \
                 patch.object(server, "save_mcp_vault") as mock_save:
                summary = await server.restore_configurations_bundle(bundle_body)
                self.assertIn("remote-oauth", summary["mcps"]["restored"])

                # Check that remote-oauth was added to vault with authenticated: False (clean re-auth required)
                self.assertIn("remote-oauth", mock_vault)
                srv = mock_vault["remote-oauth"]
                self.assertEqual(srv["auth_mode"], "oauth2")
                self.assertEqual(srv["client_id"], "my-client")
                self.assertFalse(srv["authenticated"])

        asyncio.run(_test())


if __name__ == "__main__":
    unittest.main()
