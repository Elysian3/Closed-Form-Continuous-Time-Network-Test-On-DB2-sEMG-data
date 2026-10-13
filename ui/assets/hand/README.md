# Rigged human hand assets

Project stage paused on 2026-10-13; see the [closeout record](../../../docs/project_closeout.md). These assets remain guidance/rendering resources; their presence does not establish anatomical calibration or real EMG prediction. Upstream attribution and license below are retained.

Source: [Immersive Web / WebXR Input Profiles, generic-hand](https://github.com/immersive-web/webxr-input-profiles/tree/c5356912d0c3bc43c6db8aa3926d8951a7716449/packages/assets/profiles/generic-hand).

- `left.glb`, `right.glb`: unmodified upstream GLB files, downloaded 2026-09-10.
- Upstream path revision: `c5356912d0c3bc43c6db8aa3926d8951a7716449`.
- Asset license: MIT; full notice in `LICENSE.md`. Upstream [asset README](https://github.com/immersive-web/webxr-input-profiles/blob/main/packages/assets/README.md) explicitly covers the glTF/GLB assets.
- Each mesh: 1,360 vertices, 2,314 triangles, 25 skin joints. This is a continuous rigged hand surface, not assembled primitives.
- Original mesh material is neutral gray without image textures. The renderer applies warm skin shading; this is not a photographic scan or an anatomically validated biomechanics model.
- Both models are served locally. No Tripo generation or paid generation credits were used.

SHA-256:

```text
left.glb   bc67783144944ea1cda54d9247885825ea5fb9d4651469fe7d00be517a5c2b87
right.glb  291790c14f7f88a7f9bd35330c47392ed8e8d395ae6728f4bb7089f1bc1f2b96
```
