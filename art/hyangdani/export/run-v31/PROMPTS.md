# Run v31 — artwork correction record (Phase 2.2.1, WO-01)

The Run cycle delivered as Stage 2 (`hyangdani-run-v3-4x3.png`) was replaced for
Phase 2.2.1. The old atlas is untouched in the repository; the new one ships as
`hyangdani-run-v31-4x3.png` (service, 384 px cells) built from
`hyangdani-run-v31-4x3-768.png` (authoring, 768 px cells) in this folder.

## What was wrong with the previous cycle

Measured with `tools/wo0221_asset_audit.py` on the v3 atlas:

| Defect | Measurement on `hyangdani-run-v3-4x3.png` |
| --- | --- |
| No bounce: every frame pinned to the same sole line | all 12 frames had their lowest alpha row at y=762/768 |
| No airborne phase, so the cycle read as a slide | 0 frames above the contact line |
| Character height jumping between frames | 679–753 px (11% spread) |
| No leg alternation: the same half-cycle repeated with variations | the near leg did the same knee-lift in every frame |
| Loop seam | frame 11 → frame 0 sat below the cycle's average frame-to-frame similarity |

## What the new cycle does

Twelve keyframes on the standard two-step run cycle (6 frames per step,
12 fps, 1.0 s loop). Each phase is a separate prompt, and the phases alternate
legs so the second half is the other step, not a copy.

| Frame | Phase | Ground | Hair / arm inertia |
| --- | --- | --- | --- |
| 0 | contact, right foot strikes, front leg nearly straight | sole on floor | hair flicking up from the speed |
| 1 | down, knee bends absorbing, hips lowest | sole on floor | hair whipped up and loose (body dropped) |
| 2 | passing, left knee drives past the support leg | sole on floor | hair loosening upward |
| 3 | push-off, right leg straight back, toe leaving | toe on floor (2 px) | hair swept flat back |
| 4 | airborne, legs scissored | lifted 22 px | hair floating out, tips curling up |
| 5 | reach, left leg reaches for the next strike | lifted 8 px | hair still floating, tipping forward |
| 6–11 | the same six phases on the left foot | as above | as above |

The bounce is not baked as a per-frame offset: the poses themselves carry it
(bent support leg = low hips), and only the airborne/reach frames are lifted off
the floor line, so the contact frames stay planted.

## Pipeline

1. Generate each keyframe from the delivered Run frame `f0` as the image edit
   base, with the character canon, camera and hammer arm held fixed, on a flat
   `#00FF00` chroma background.
2. `tools`-side compose step (kept in this folder's record, not a repo tool):
   chroma key + despill, one global scale to the delivered canon
   (768 px cell, ~715 px character, hammer head ≈125 px), horizontal anchor on
   the hammer head's inner edge (x≈570), vertical anchor on the sole with the
   per-phase lift above, and clip protection inside the cell.
3. Frames exported to `frames/`, the 768×2304 atlas to
   `hyangdani-run-v31-4x3-768.png`, the 384 service atlas built with
   `python3 tools/build_sprite_atlases.py`.

Only frame 2 carries a 1.8% scale correction: with the flight hair at full
stretch its tips reached the top cell border.

## Files

| Role | Path | SHA-256 |
| --- | --- | --- |
| Authoring atlas 3072×2304 | `hyangdani-run-v31-4x3-768.png` | `06af5814ad83915b42282cfe738f9c06ba687be80c024e70cb39f4c08e23a389` |
| Service atlas 1536×1152 | `static/public/assets/sprites/hyangdani-run-v31-4x3.png` | `38b6a5e222b31349665c9f7ffaacd993b3f8d4f065dae8748eafaeba0c88be16` |
| Individual frames (768 px cells) | `frames/hyangdani-run-v31-00.png` … `-11.png` | below |

```text
7a884272ac099e73afe2352cc556549150a769da37ce4de4f740eb7914b39101  hyangdani-run-v31-00.png
f0c8e45e352b67d7262c5d00bb78ba9d5fe0b0e75b45561ddd5879693e05927c  hyangdani-run-v31-01.png
ac64efc1b7a140a190ae4ab525df186715951ea758cf3ca7dc5a90bf161d3e36  hyangdani-run-v31-02.png
6befffe4b7d28da5c3bcb4bcf987a560eff3c4b4423ef8f6a1b025eeadd0e26e  hyangdani-run-v31-03.png
4d842fdf4a2f0d6552ad07e5dc6b1bc286831ce9416929f002528f2bd27f87e5  hyangdani-run-v31-04.png
0b22be52781fa40d663d2de89a98a0d5e71a40590a625800220c9f3f492321be  hyangdani-run-v31-05.png
5592ffacf07e739eee4be868b3390c97b2790632c8af7fb710f6d6971290892a  hyangdani-run-v31-06.png
6213d1dc6fb5363fddedf64726793378df50be66b09ab3e10a314740524b401a  hyangdani-run-v31-07.png
1fd2e13691d79cded55aafa24256806d4cc51318e7adf49ef607a2a3d7c3e13a  hyangdani-run-v31-08.png
356cacf276b20cba2e882e53cbff401e30172ce887b2706dd8201831118f15e5  hyangdani-run-v31-09.png
d9a8c4056c1672704204657965270f28fab18065777f676ea76fbe5a8c2a7e64  hyangdani-run-v31-10.png
145275acf4ba9fe9a8ce9b52efae3b21aec9cf6667fd79733f61dd576a6a10e9  hyangdani-run-v31-11.png
```

The untouched 1254×1254 generator outputs stay outside Git in
`art/hyangdani/source/run-v31/raw-f00-1254.png` … `raw-f11-1254.png`
(`art/.gitignore` keeps `*/source/*` out of the repository). They are only the
generator originals; the delivered drawings are the 768 px frames above.

## Prompts

All twelve share this frame (the `{pose}` and `{hair}` slots are the table above):

```text
Redraw this exact character in this exact art style and keep the face, lavender-silver
twin-bun hair, white techwear jacket, black crop top, white cargo pants with black straps,
purple sneakers, headphone collar at the neck and purple neon cyber hammer identical.
Same 3/4 view facing right, same camera angle and lens, same body proportions, full body
visible from the top of the hair to the sole of the shoes, nothing cropped, character
centered. The huge hammer stays in her right hand, arm extended to the side, only a small
swing. Change only the pose to this running keyframe: {pose} Hair: {hair}. Flat solid pure
green #00FF00 background, no shadow, no floor, no ground line, no text, no frame numbers.
```

### Per-frame pose / hair slots

```text
f0  CONTACT pose of a right-leg strike: her right leg is extended forward and nearly straight
    with the front foot planted flat, the left leg is lifted behind her body with the knee bent
    and the left shoe high up behind, torso leaning forward, left arm swinging forward
    Hair: long hair trailing behind her with the tips flicking upward from the speed
f1  DOWN pose right after the strike: her right leg is bent sharply under the body absorbing
    the landing while the foot stays flat, her left knee swings forward close to the body with
    the left foot just off the ground behind her, hips at their lowest, shoulders dipped, torso
    compressed
    Hair: long hair whipped up and floating high and loose because the body suddenly dropped
f2  PASSING pose: her left knee is driven high in front of her body with the left foot lifted,
    her right leg is straight and bearing her weight under the hip, hips rising, torso upright
    with a slight forward lean, left arm swinging back
    Hair: long hair starting to trail behind her, still loosened upward
f3  PUSH-OFF pose: her right leg is fully extended straight back with the toe pointed and the
    shoe just leaving the ground, her left knee is driven up high in front, body stretched and
    tall, hips at their highest, chest open, left arm forward
    Hair: long hair streaming straight back in a long flat sweep
f4  AIRBORNE suspension pose: both feet are clearly off the ground, legs scissored, left knee up
    in front and right leg folded underneath with the right shoe behind, hips high, body tight,
    ready to land on the left foot
    Hair: long hair floating up and out with a slight upward curl at the tips
f5  REACH pose before the next strike: her left leg is swinging forward and extended with the
    left shoe reaching ahead, the toes lifted up off the ground, her right leg trails behind
    with the knee bent, hips dropping, torso leaning forward
    Hair: long hair still floating and slightly forward as the body falls
f6  CONTACT pose of a left-leg strike, the other step: her left leg is extended forward and
    nearly straight with the front foot planted flat, the right leg is lifted behind her body
    with the knee bent and the right shoe high up behind, torso leaning forward, left arm back
    Hair: long hair trailing behind her with the tips flicking upward from the speed
f7  DOWN pose right after the left-foot strike: her left leg is bent sharply under the body
    absorbing the landing while the foot stays flat, her right knee swings forward close to the
    body with the right foot just off the ground behind her, hips at their lowest, shoulders
    dipped, torso compressed
    Hair: long hair whipped up and floating high and loose because the body suddenly dropped
f8  PASSING pose of the other step: her right knee is driven high in front of her body with the
    right foot lifted, her left leg is straight and bearing her weight under the hip, hips
    rising, torso upright with a slight forward lean, left arm forward
    Hair: long hair starting to trail behind her, still loosened upward
f9  PUSH-OFF pose of the other step: her left leg is fully extended straight back with the toe
    pointed and the shoe just leaving the ground, her right knee is driven up high in front,
    body stretched and tall, hips at their highest, chest open
    Hair: long hair streaming straight back in a long flat sweep
f10 AIRBORNE suspension pose of the other step: both feet are clearly off the ground, legs
    scissored, right knee up in front and left leg folded underneath with the left shoe behind,
    hips high, body tight, ready to land on the right foot
    Hair: long hair floating up and out with a slight upward curl at the tips
f11 REACH pose before the loop restarts: her right leg is swinging forward and extended with the
    right shoe reaching ahead, the toes lifted up off the ground, her left leg trails behind
    with the knee bent, hips dropping, torso leaning forward
    Hair: long hair still floating and slightly forward as the body falls
```
