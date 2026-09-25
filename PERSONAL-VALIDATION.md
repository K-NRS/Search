# Search Personal — validated package

## Exact build

- Application source: `bf0fa7d997f867d2712354a0c2f6c2d1a65e70c0`.
- Upstream main included: `491f3214063212fac176a7ad95467f0821040451`, still current when checked on 2026-09-25.
- All six PR histories retained: #54, #190, #192, #225, #227, #231.
- Original fork main and the six source branches remain unchanged.
- Search Personal 1.0.3, build 202609251313; minimum macOS 14, extensions require macOS 15.4+.
- Universal executable: Apple Silicon arm64 and Intel x86_64. Runtime checks used Apple Silicon; Intel was compiled and its slice verified, not runtime-tested.
- Ad-hoc signature verified; this is not an Apple-notarized or Developer-ID-signed release.

## Download and install

Get `Search-Personal-universal-macOS` from [build run 36139504658](https://github.com/K-NRS/Search/actions/runs/36139504658), extract the artifact and then `Search-Personal-universal.zip`. Move **Search Personal.app** to `/Applications` or `~/Applications`. No local compilation or Codex session is required.

The ZIP is 4,032,698 bytes. SHA-256:

```text
b9a162e66940e0efe5c6157b2202124759e2cee5f72ab8ecbd72c051d9190bab
```

If macOS blocks the first launch, review the source/checksum and allow this specific app under System Settings > Privacy & Security > Open Anyway. Do not disable Gatekeeper globally.

## Completed validation

Debug compilation, release compilation for both architectures, universal packaging and strict code-signature verification succeeded. The completed binary was downloaded and checked against the CI-produced SHA256SUMS; its two Mach-O slices and identity flags were independently inspected.

The exact downloaded app was then exercised on a fresh macOS runner **without SEARCH_PROBE and without enabling scripted control**. [Production package validation run 36140743378](https://github.com/K-NRS/Search/actions/runs/36140743378) passed all 14 checks:

1. Distinct production bundle ID.
2. Distinct production profile configuration.
3. Upstream updates disabled in the package.
4. Downloaded application signature verifies.
5. Both Mac architectures are present.
6. Fresh fixture paths, with no existing user data.
7. Normal application stays running.
8. Personal saved session is restored and a real local webpage is rendered.
9. Real page form state is retained.
10. Navigation to a second real page succeeds.
11. Website storage survives navigation.
12. An overdue upstream update check does not run.
13. Synthetic original and legacy browser files remain unchanged.
14. Scripted browser control stays disabled without user consent.

The first page reported a laid-out width of 1008 pixels, the expected DOM text and its form value. The second page reported the expected DOM text and retained localStorage value.

## Coverage limits

As of this report (2026-09-25 13:29 UTC), the separate 15-suite feature-regression run was still in progress. Its passing or completed status is **not** claimed here; consult that run's structured summary when available. Historical counts in the original PR descriptions are not results for this combined build. Live authenticated Netflix playback and every interactive workflow are not verified by the 14-check production smoke.

An initial smoke fixture tried to enable scripted control through defaults alone and could not connect. The successful fixture instead exercises ordinary startup and local page observations; no consent gate was weakened.

## Data safety

Bundle ID: `tech.noras.search.personal`. Profile: `~/Library/Application Support/Search Personal/`. Passwords use a separate keychain label/security domain. No automatic migration, overwrite, or import of the installed upstream browser's data takes place. Existing tabs, extensions, cookies, history and logins therefore do not automatically appear in this new profile.

Upstream self-updates are disabled so an official update cannot replace these personal features. Installation does not select the app as the default browser. See [PERSONAL.md](PERSONAL.md) for source-build details.
