"""
relations.py — the CV side of the VLM-first architecture: a frame -> the per-frame
TRUTH VECTOR over relation_table.md rows 1-11.

The VLM planner picks WHICH conjunctions matter (planner.py); this module answers, every
frame, WHETHER each relation currently holds. All geometry, no learning, no depth:
  rows 1-4   reuse gaze.py (head-pose rays, arm rays — already unit-tested)
  rows 5-11  new geometry over PoseLandmarker keypoints + detector boxes; the stateful
             rows (7 approach, 9 sustain, 10 count-change, 11 handoff) keep small
             cross-frame state inside RelationEngine, all time-based (fps-independent).

Scale trick (no depth camera): SHOULDER WIDTH is the per-person metric ruler. Hall's
zones are defined in metres; we express them in shoulder-widths (≈0.45 m each), so
"personal zone" ≈ 2.7 shoulder widths. Crude, monotone, and honest — a calibration
constant, not a theory claim (grounding_map.md's "engineering (tune)" category).

Usage:
    eng = RelationEngine(detector)               # detector from perceive.make_detector
    truth, viz = eng.step(frame_bgr)             # truth: {1..11: bool}; viz for overlay
"""

from __future__ import annotations
import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from perception.perceive import Detection
from perception.gaze import (HeadPoseEstimator, GazeRay, arm_ray_from_points, gazing_at,
                  pointing_at, joint_attention, eye_contact, ray_hits_box, _mp_vision,
                  _ensure_model, pose_head_ray)

Box = Tuple[float, float, float, float]

# landmark ids (PoseLandmarker, 33 pts)
NOSE, L_SH, R_SH, L_EL, R_EL, L_WR, R_WR, L_HIP, R_HIP = 0, 11, 12, 13, 14, 15, 16, 23, 24
_NEEDED = (NOSE, L_SH, R_SH, L_EL, R_EL, L_WR, R_WR, L_HIP, R_HIP)

# objects that plausibly act as a SHARED artifact for turn-taking (row 11)
ARTIFACT_TYPES = {"laptop", "keyboard", "mouse", "book", "cell phone", "phone", "remote",
                  "tablet", "cup", "bottle", "scissors", "toy"}

SHOULDER_M = 0.45          # metres per shoulder width (the ruler)
ZONE_PERSONAL = 1.2 / SHOULDER_M   # Hall personal-zone radius, in shoulder widths (~2.7)
ZONE_SOCIAL = 3.6 / SHOULDER_M     # social-zone outer radius (~8)


# --------------------------------------------------------------------------- #
@dataclass
class PersonPose:
    pid: int                                   # stable id from the tracker
    pts: Dict[int, Tuple[float, float]]        # landmark -> px
    vis: Dict[int, float]
    raw: Optional[list] = None                 # all 33 landmarks [(x,y,vis)] (skeleton draw + head-ray fallback)

    def has(self, *ids, min_vis=0.5):
        return all(i in self.pts and self.vis.get(i, 0) >= min_vis for i in ids)

    @property
    def mid_hip(self):
        a, b = self.pts[L_HIP], self.pts[R_HIP]
        return ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)

    @property
    def mid_shoulder(self):
        a, b = self.pts[L_SH], self.pts[R_SH]
        return ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)

    @property
    def shoulder_w(self):
        a, b = self.pts[L_SH], self.pts[R_SH]
        return max(1.0, math.hypot(a[0] - b[0], a[1] - b[1]))

    @property
    def box(self) -> Box:
        xs = [p[0] for p in self.pts.values()]
        ys = [p[1] for p in self.pts.values()]
        return (min(xs), min(ys), max(xs), max(ys))


class PoseEstimator:
    """frame -> List[PersonPose] (ids NOT yet assigned; the tracker does that)."""

    def __init__(self, max_people: int = 4, min_conf: float = 0.5, smooth: float = 0.5):
        mp, mp_tasks, vision = _mp_vision()
        self._mp = mp
        self._lm = vision.PoseLandmarker.create_from_options(vision.PoseLandmarkerOptions(
            base_options=mp_tasks.BaseOptions(model_asset_path=_ensure_model("pose_landmarker_lite.task")),
            running_mode=vision.RunningMode.VIDEO, num_poses=max_people,
            min_pose_detection_confidence=min_conf,
            min_pose_presence_confidence=0.3,    # lower -> person stops flickering OUT when still
            min_tracking_confidence=0.3))        # lower -> keeps the track alive between detections
        self._ts = 0
        self.smooth = smooth                     # EMA factor (lower = smoother, more lag)
        self._prev: List[list] = []              # previous smoothed full-landmark arrays

    def estimate(self, frame_bgr) -> List[PersonPose]:
        import cv2
        H, W = frame_bgr.shape[:2]
        img = self._mp.Image(image_format=self._mp.ImageFormat.SRGB,
                             data=cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB))
        self._ts += 33
        res = self._lm.detect_for_video(img, self._ts)
        raw_all = [[(lm.x * W, lm.y * H, getattr(lm, "visibility", 1.0) or 1.0) for lm in person]
                   for person in (res.pose_landmarks or [])]
        # TEMPORAL SMOOTHING (EMA, matched by person index) — stops a still person's joints jittering.
        a = self.smooth
        sm = []
        for i, pose in enumerate(raw_all):
            if i < len(self._prev) and len(self._prev[i]) == len(pose):
                prev = self._prev[i]
                pose = [(a * x + (1 - a) * px, a * y + (1 - a) * py, v)
                        for (x, y, v), (px, py, _) in zip(pose, prev)]
            sm.append(pose)
        self._prev = sm
        out = []
        for raw in sm:
            pts = {i: (raw[i][0], raw[i][1]) for i in _NEEDED}
            vis = {i: raw[i][2] for i in _NEEDED}
            out.append(PersonPose(pid=-1, pts=pts, vis=vis, raw=raw))
        return out

    def close(self):
        self._lm.close()


class Tracker:
    """Nearest-mid-hip identity across frames — enough for handoff/approach state."""

    def __init__(self, max_jump_frac: float = 0.25, forget_s: float = 3.0):
        self.max_jump_frac, self.forget_s = max_jump_frac, forget_s
        self.known: Dict[int, Tuple[Tuple[float, float], float]] = {}  # pid -> (pos, t)
        self._next = 1

    def assign(self, poses: List[PersonPose], wh, t) -> List[PersonPose]:
        W, H = wh
        max_jump = self.max_jump_frac * math.hypot(W, H)
        for pid in [p for p, (_, seen) in self.known.items() if t - seen > self.forget_s]:
            del self.known[pid]
        free = dict(self.known)
        for pose in poses:
            mh = pose.mid_hip
            best, bd = None, max_jump
            for pid, (pos, _) in free.items():
                d = math.hypot(mh[0] - pos[0], mh[1] - pos[1])
                if d < bd:
                    best, bd = pid, d
            if best is None:
                best = self._next; self._next += 1
            else:
                free.pop(best)
            pose.pid = best
            self.known[best] = (mh, t)
        return poses


# --------------------------------------------------------------------------- #
class RelationEngine:
    """One step = one frame -> truth vector {1..11} + viz payload. Holds ALL state."""

    def __init__(self, detector, max_people=4,
                 tol_gaze=12.0, tol_mutual=25.0,
                 approach_win_s=3.0, approach_frac=0.20,
                 lean_deg=25.0, lean_near_sw=5.0, sustain_s=1.0, event_hold_s=5.0):
        self.det = detector
        self.faces = HeadPoseEstimator(max_faces=max_people)   # face/gaze ALWAYS MediaPipe
        self.poses = PoseEstimator(max_people=max_people)
        self.tracker = Tracker()
        self.tol_gaze, self.tol_mutual = tol_gaze, tol_mutual
        self.approach_win_s, self.approach_frac = approach_win_s, approach_frac
        self.lean_deg, self.sustain_s, self.event_hold_s = lean_deg, sustain_s, event_hold_s
        self.lean_near_sw = lean_near_sw                 # lean must be near a watched object (shoulder-widths)
        # cross-frame state
        self._dist_hist: Dict[int, deque] = {}          # pid -> deque[(t, shoulder_w, {label: ndist})]
        self._touch_since: Dict[Tuple[int, str], float] = {}   # (pid, label) -> first-touch t
        self._count_hist: deque = deque()               # (t, n_people)
        self._count_stable: Optional[int] = None
        self._gather_until = -1e9
        # artifact -> [controller_pid|None, candidate_pid|None, candidate_since]
        self._controller: Dict[str, list] = {}
        self._handoff_until = -1e9
        self._pid_seen: Dict[int, float] = {}           # pid -> last time seen in frame

    # ---- helpers ----
    @staticmethod
    def _wrist_on(pose: PersonPose, box: Box, margin: float) -> bool:
        for w in (L_WR, R_WR):
            if w in pose.pts:
                x, y = pose.pts[w]
                if (box[0] - margin <= x <= box[2] + margin
                        and box[1] - margin <= y <= box[3] + margin):
                    return True
        return False

    def _mutual_facing(self, rays: List[GazeRay], pa: PersonPose, pb: PersonPose) -> bool:
        """F-formation PROXY: each person's head ray hits the other's body box (wide tol).
        Head direction stands in for body orientation (v1; Kendon is about bodies)."""
        def ray_of(p):
            bx = p.box
            for r in rays:
                if bx[0] <= r.origin[0] <= bx[2] and bx[1] <= r.origin[1] <= bx[3]:
                    return r
            return None
        ra, rb = ray_of(pa), ray_of(pb)
        return (ra is not None and rb is not None
                and ray_hits_box(ra, pb.box, self.tol_mutual) is not None
                and ray_hits_box(rb, pa.box, self.tol_mutual) is not None)

    # ---- the step ----
    def step(self, frame_bgr, t: Optional[float] = None):
        import cv2
        t = time.time() if t is None else t
        H, W = frame_bgr.shape[:2]
        rays = self.faces.estimate(frame_bgr)
        people = self.poses.estimate(frame_bgr)
        if any(p.pid < 0 for p in people):        # backend without ids (mediapipe) -> our tracker
            people = self.tracker.assign(people, (W, H), t)
        # DISTANCE FALLBACK: FaceMesh drops small/far faces; add a coarse Pose head ray for any
        # person the FaceMesh didn't cover (Pose detects heads much farther). Near faces keep the
        # precise FaceMesh ray. Coarse rays have no 3D, so eye_contact (row 3) auto-skips them.
        for p in people:
            if p.raw is None:
                continue
            hr = pose_head_ray(p.raw)
            if hr is None:
                continue
            thr = max(50.0, 0.5 * (hr.face_box[2] - hr.face_box[0]))
            if any((not r.coarse) and math.hypot(r.origin[0] - hr.origin[0],
                    r.origin[1] - hr.origin[1]) < thr for r in rays):
                continue
            rays.append(hr)
        dets = self.det.detect(frame_bgr) if self.det else []
        return self.evaluate(rays, people, dets, (W, H), t)

    def evaluate(self, rays: List[GazeRay], people: List[PersonPose],
                 dets: List[Detection], wh, t: float):
        """Pure-ish core (separable for tests): inputs -> truth + viz."""
        W, H = wh
        truth = {i: False for i in range(1, 12)}
        viz: Dict = {"rays": rays, "people": people, "dets": dets, "hits": []}
        for p in people:
            self._pid_seen[p.pid] = t

        # ---- 1 gazing-at, 2 joint-attention, 3 eye-contact, 4 pointing (gaze.py) ----
        g_hits = gazing_at(rays, dets, tol_deg=self.tol_gaze) if dets else []
        truth[1] = bool(g_hits); viz["hits"] += [("gazing-at", h) for h in g_hits]
        ja = joint_attention(rays, wh)
        truth[2] = bool(ja); viz["joint"] = ja
        truth[3] = any(eye_contact(r) for r in rays)
        arms = []
        for p in people:
            for side, (s, e, w) in (("left", (L_SH, L_EL, L_WR)), ("right", (R_SH, R_EL, R_WR))):
                if p.has(s, e, w):
                    a = arm_ray_from_points(p.pts[s], p.pts[e], p.pts[w], side=side)
                    if a:
                        arms.append(a)
        p_hits = pointing_at(arms, dets) if dets else []
        truth[4] = bool(p_hits); viz["arms"] = arms
        viz["hits"] += [("pointing-at", h) for h in p_hits]

        pairs = [(a, b) for i, a in enumerate(people) for b in people[i + 1:]
                 if a.has(L_SH, R_SH, L_HIP, R_HIP) and b.has(L_SH, R_SH, L_HIP, R_HIP)]

        # ---- 5 proxemic zone (personal or closer) ----
        for a, b in pairs:
            sw = (a.shoulder_w + b.shoulder_w) / 2
            d = math.hypot(a.mid_hip[0] - b.mid_hip[0], a.mid_hip[1] - b.mid_hip[1]) / sw
            if d <= ZONE_PERSONAL:
                truth[5] = True; viz.setdefault("close_pairs", []).append((a.pid, b.pid, d))

        # ---- 6 F-formation (mutual facing proxy + within social zone) ----
        for a, b in pairs:
            sw = (a.shoulder_w + b.shoulder_w) / 2
            d = math.hypot(a.mid_hip[0] - b.mid_hip[0], a.mid_hip[1] - b.mid_hip[1]) / sw
            if d <= ZONE_SOCIAL and self._mutual_facing(rays, a, b):
                truth[6] = True; viz.setdefault("fform", []).append((a.pid, b.pid))

        # ---- 7 approach / depart (camera via shoulder-width trend; objects via ndist) ----
        for p in people:
            if not p.has(L_SH, R_SH):
                continue
            nd = {}
            for dt_ in dets:
                cx, cy = (dt_.box[0] + dt_.box[2]) / 2, (dt_.box[1] + dt_.box[3]) / 2
                nd[dt_.label] = math.hypot(p.mid_hip[0] - cx, p.mid_hip[1] - cy) / p.shoulder_w
            hist = self._dist_hist.setdefault(p.pid, deque())
            hist.append((t, p.shoulder_w, nd))
            while hist and t - hist[0][0] > self.approach_win_s:
                hist.popleft()
            if len(hist) >= 3:
                w0, w1 = hist[0][1], hist[-1][1]
                if w1 >= w0 * (1 + self.approach_frac) or w1 <= w0 * (1 - self.approach_frac):
                    truth[7] = True                       # toward/away from the camera
                else:
                    nd0 = hist[0][2]
                    for lab, v in nd.items():
                        if lab in nd0 and (v <= nd0[lab] * (1 - 1.5 * self.approach_frac)
                                           or v >= nd0[lab] * (1 + 1.5 * self.approach_frac)):
                            truth[7] = True
                            viz.setdefault("approach", []).append(lab)
                            break

        # ---- 8 lean-in (torso tilt off image-vertical AND near a WATCHED object) ----
        # bare torso tilt (seated / turned / fisheye edge) is not a lean; require the tilt to be
        # next to a detected object, and bind to it (so it's a real "leaning at something").
        for p in people:
            if p.has(L_SH, R_SH, L_HIP, R_HIP):
                ms, mh = p.mid_shoulder, p.mid_hip
                ang = abs(math.degrees(math.atan2(ms[0] - mh[0], -(ms[1] - mh[1]))))
                if ang > self.lean_deg:
                    near, nd = None, 1e9
                    for dt in dets:
                        if dt.label == "person":
                            continue
                        cx, cy = (dt.box[0] + dt.box[2]) / 2, (dt.box[1] + dt.box[3]) / 2
                        d = math.hypot(cx - mh[0], cy - mh[1]) / max(1.0, p.shoulder_w)
                        if d < nd:
                            nd, near = d, dt
                    if near is not None and nd <= self.lean_near_sw:
                        truth[8] = True
                        viz["hits"].append(("lean-in", {"det": dets.index(near)}))

        # ---- 9 hands-on (wrist in object box, SUSTAINED >= sustain_s) ----
        active = set()
        for p in people:
            for dt_ in dets:
                if dt_.label == "person":
                    continue
                margin = 0.10 * math.hypot(dt_.box[2] - dt_.box[0], dt_.box[3] - dt_.box[1])
                if self._wrist_on(p, dt_.box, margin):
                    key = (p.pid, dt_.label)
                    active.add(key)
                    since = self._touch_since.setdefault(key, t)
                    if t - since >= self.sustain_s:
                        truth[9] = True
                        viz.setdefault("handson", []).append(key)
        for key in list(self._touch_since):
            if key not in active:
                del self._touch_since[key]

        # ---- 10 gathering (stable person-count change; T held for event_hold_s) ----
        self._count_hist.append((t, len(people)))
        while self._count_hist and t - self._count_hist[0][0] > 1.5:
            self._count_hist.popleft()
        counts = [n for _, n in self._count_hist]
        if counts and counts.count(counts[-1]) == len(counts):    # stable over the window
            n = counts[-1]
            if self._count_stable is None:
                self._count_stable = n
            elif n != self._count_stable:
                self._count_stable = n
                self._gather_until = t + self.event_hold_s
        truth[10] = t < self._gather_until

        # ---- 11 turn-taking / control handoff (controller of a shared artifact changes) ----
        for dt_ in dets:
            if dt_.label not in ARTIFACT_TYPES:
                continue
            margin = 0.10 * math.hypot(dt_.box[2] - dt_.box[0], dt_.box[3] - dt_.box[1])
            holders = [p.pid for p in people if self._wrist_on(p, dt_.box, margin)]
            st = self._controller.setdefault(dt_.label, [None, None, t])
            if len(holders) == 1:
                h = holders[0]
                if h == st[0]:                              # same controller: clear candidate
                    st[1] = None
                elif st[0] is None:                         # first ever holder takes control
                    st[0] = h; st[1] = None
                elif st[1] != h:                            # a NEW hand arrives: start its clock
                    st[1], st[2] = h, t
                elif t - st[2] >= self.sustain_s:           # new holder sustained
                    # guard against TRACKER ID CHURN: a real handoff means the previous
                    # controller is another person who is still around. If the old pid
                    # vanished (same human re-identified), transfer control SILENTLY.
                    if t - self._pid_seen.get(st[0], -1e9) <= 2.0 and h != st[0]:
                        viz.setdefault("handoff", []).append((dt_.label, st[0], h))
                        self._handoff_until = t + self.event_hold_s
                    st[0], st[1] = h, None
            # zero or multiple holders: controller unchanged, candidate keeps its clock
        truth[11] = t < self._handoff_until

        return truth, viz

    def close(self):
        self.faces.close(); self.poses.close()
