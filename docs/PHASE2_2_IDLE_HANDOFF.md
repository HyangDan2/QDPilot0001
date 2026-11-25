# Phase 2.2 — Idle asset handoff (partial)

This branch contains the verified import utility, not the binary atlas yet. The delivery bundle is provided separately. Do not enable a missing source in manifest.json.

## Actual delivery

- Idle: 8 distinct generated frames, right-facing, 8 FPS, 1-second loop.
- Source sheet: 1774×887 RGBA; native cells approximately 443–444 px, resampled to 768×768 authoring canvases.
- Authoring atlas: 3072×1536; runtime atlas: 1536×768 (4×2, 384 px cells).
- Runtime file: static/public/assets/sprites/hyangdani-idle-v3-4x2.png, 773172 bytes.
- SHA-256: 878b44b6e41538061b6b4c794a5f942cf1bdd383bf4f6ce5ef4a802157543405.
- Anchor: [0.5, 707/768]. Eight distinct frame hashes, RGBA alpha, no solid pixels at cell borders. Visual inspection on dark background completed.
- Run / Attack / Repair / LevelUp / Install: 56 frames not delivered. Keep existing fallbacks.
- No production game E2E or Pi benchmark performed. No main merge or deploy.

## Import

From a clean checkout of this branch, with the delivered ZIP available:

```sh
python3 tools/install_phase22_idle.py --bundle /absolute/path/idle_ops_phase22_idle.zip --check
python3 tools/install_phase22_idle.py --bundle /absolute/path/idle_ops_phase22_idle.zip
node --test tests/js/sprite_manifest.test.mjs tests/js/op_coordinator.test.mjs
python3 -m unittest discover -s tests -t .
git diff -- static/public/assets/sprites/manifest.json
git add static/public/assets/sprites/hyangdani-idle-v3-4x2.png static/public/assets/sprites/manifest.json
```

The script validates the actual PNG by SHA-256, dimensions and RGBA format, refuses conflicting sources, writes the asset before switching the Idle manifest source, and preserves the existing fallback and every other state. Local isolated fixture tests covered dry run, import, repeat import, corrupted asset, conflicting source and other-state preservation. Actual browser tests and Git upload of the binary remain pending.

## Release checkpoints

- TP-2201: character concept master prepared.
- TP-2202: actual Idle 8F delivered separately.
- TP-2203: atlas and alpha validation complete locally.
- TP-2204: Manifest v3 import utility implemented; actual binary import not yet executed in repository.
- TP-2205: remaining 56 frames pending.
- TP-2206: game E2E, Pi performance, release approval pending.

Do not merge or deploy before owner review. Keep Qoder backend work separate.
