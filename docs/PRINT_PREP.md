# 3D-Print Prep Spec — 3-DOF Sidekick (pan / tilt / nod, 3× Feetech SCS0009)

The Blender shell is a **massing model** (form only). Printable parts are built in Rhino/Fusion from this spec: real servo pockets, horn interfaces, bearings, screw bosses, tolerances, and split lines. Verify every servo/camera dimension against its datasheet before printing.

## Component dimensions (verify!)
- **SCS0009** bus servo: body ≈ 23.0 × 12.0 × 22.0 mm; two mounting tabs (span ≈ 32 mm, ~2.5 mm thick, 2 holes ~ Ø2); output spline on top face, offset from center; comes with horns + center screw. Stall ~1.3 kg·cm @6V. 3-wire TTL bus (daisy-chain: pan ID1, tilt ID2, nod ID3).
- **Camera** Waveshare OV2735: PCB 25 × 25 mm (mount holes 21 mm pitch, Ø2), lens barrel Ø14 mm, protrudes ~16 mm, total depth 22 mm, FOV 96°, USB-C or 4-pin.

## Servo mounting — the universal rule
Each joint = **(a) body captured in a pocket + (b) horn drives the rotating part + (c) a passive pivot on the opposite side of the axis.** Never let the servo spline be the only support for a load.

Print each servo pocket to **body +0.3 mm** clearance; secure with the 2 mounting-tab screws into **M2 heat-set inserts** (don't screw straight into PLA). Use the Feetech-supplied horn as an embedded interface: screw your printed rotating part to the horn, then the horn clips onto the spline.

### Pan (in the base)
- Body vertical, spline up, captured in a pocket in the **fixed lower base**.
- Horn screws to the underside of the **rotating hub**.
- **Load support (critical, SCS0009 is weak):** add a large-diameter **turntable ring race** between fixed base and rotating hub — two printed rings with a groove for Ø4–5 mm airsoft BBs, or an off-the-shelf thin-section bearing / lazy-susan. The servo then supplies torque only.
- Split line: base = `base_lower` (servo + electronics + BB race lower half) + `hub` (rotating, race upper half).

### Tilt (in the joint hub)
- Body horizontal in the hub, spline on +X side.
- Horn drives the **outer telescoping tube** (fixed to the tilt output).
- Opposite (−X) side: **passive pivot** — a Ø3 pin into a printed boss with a small bearing or PTFE washer, coaxial with the spline.
- Hub is a 2-part clamshell (split on the XY plane) so the servo drops in.

### Nod (top of the inner tube)
- Body horizontal at the top of the **inner tube**, spline on +X.
- Horn drives the **head assembly** (head + camera live here permanently).
- Opposite side: passive pin/bearing.
- Nod servo + head form one non-removable unit (so neck length changes never disturb wiring at the head).

## Shell split into printable parts
1. `base_lower` — pan servo pocket, MCU bay, cable exit at rear, BB-race lower.
2. `hub` — BB-race upper + tilt servo clamshell (2 halves) + tilt passive-pivot boss.
3. `neck_outer` — outer telescoping tube, fixed to tilt horn (bayonet or 2 screws).
4. `neck_inner` — inner tube, carries nod servo + head; detent spring tab.
5. `head_front` + `head_back` — cone head split along its axis (lens hole in front; camera board captured between halves via 21 mm-pitch bosses); LED window on top-front.
Joints between parts: M2 screws into heat-set inserts, or snap-fit + screw for the head.

## Telescoping neck (3 positions: 40 / 80 / 120 mm)
Two concentric tubes, **diametral clearance 0.3–0.4 mm** (PLA-PLA binds/wears — prefer PETG for the sliding pair, or line the contact with PTFE tape).
- **Primary lock — spring detent:** a thin cantilever tab on the inner tube with a bump; three windows in the outer tube at 40/80/120 mm. Push tab to release, slide, click. Print the tab wall ~0.8–1.0 mm so it flexes without snapping.
- **Fallback — pin lock:** one hole in inner tube, three in outer; a captive Ø3 detent pin.
- Print tubes **vertically** for concentric roundness. Chamfer the inner tube's leading edge so it doesn't catch.
- **Wiring:** the nod servo's 3-wire bus runs down the inner tube; coil ~120 mm slack in the hub so the longest extension never tugs. All three servos share one bus down through the neck to the base.

## Tolerance & print checklist
- Servo pockets: body +0.3 mm; horn screw holes: tap size for M2 self-tap or insert.
- Sliding tubes: +0.35 mm diametral; test-print a 20 mm coupon pair first.
- Heat-set inserts (M2 brass) at every screw joint; don't thread PLA directly.
- Layer orientation: print the neck tubes and the stalk upright; print the head halves face-down on the split plane; the thin neck's weak axis is the layer line — keep bending loads off it or print in PETG/nylon.
- Weigh the finished head assembly; confirm < ~60 g so the nod & tilt SCS0009 hold it at the 12 cm extension (see neck_feasibility.py).

## Order of operations
1. Rhino: model each printable part from this spec around imported servo + camera dummies (draw them at true dims).
2. Print coupons: one servo pocket + one tube pair — verify fit before committing.
3. Print parts, install heat-set inserts, mount servos, set IDs (1/2/3), route the bus.
4. Assemble, hang-test the neck at 12 cm, then run the exported motion CSVs.
