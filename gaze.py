"""
gaze.py — the DIRECTION primitive for the gaze/joint-attention branch.

Object boxes alone can't express "looking at": pointing/attention relations need a RAY.
This module adds exactly that, the cheap way (see BRANCH_gaze_handoff.md):

    MediaPipe FaceMesh (per-face landmarks)  +  solvePnP (head pose)  ->  2D gaze ray
    in the image plane. No depth camera: ray-hits-box (#5 gazing-at) and multi-ray
    convergence (#6 joint-attention) are pure 2D tests; the VLM covers front/back
    ambiguity at confirm time, same split as perceive.py (cheap candidate -> VLM precision).

This is HEAD-pose gaze (where the face points), not eyeball gaze — coarser but far more
robust at room distance on a 2fps MJPEG cam, and it's the signal people actually read
socially ("she turned her head toward it"). Friesen & Kingstone 1998 cueing works with
head orientation too.

Four relations from the template table live here (picked by easy-to-trigger × theory):
  #5 gazing-at, #6 joint-attention, #4 reaching/pointing, eye-contact (looking-at-robot).

Pieces:
  - GazeRay            : nose-tip origin + 2D unit direction + yaw/pitch + 3D fwd/pos
  - HeadPoseEstimator  : frame -> List[GazeRay]   (MediaPipe FaceMesh, lazy import)
  - ArmRay/ArmRayEstimator : elbow->wrist ray, ONLY when the arm is extended
                         (PoseLandmarker, multi-person)
  - ray_hits_box       : does a ray hit a Detection box (exact slab test + angular tol);
                         duck-typed on .origin/.dir so gaze AND arm rays share it
  - gazing_at (#5)     : rays × detections -> {ray, subject, det, angle, dist}
  - pointing_at (#4)   : same machinery on arm rays (person targets allowed)
  - joint_attention (#6): ≥2 gaze rays converge in front of both -> convergence point
  - eye_contact        : 3D face-forward vs face->camera (robust where the 2D ray degenerates)
  - __main__           : live webcam/M5 test drawing ALL FOUR relations; --detect adds the
                         object detector. Run this FIRST to sanity-check.

Deps:  pip install mediapipe   (CURRENT version — uses the tasks API, legacy mp.solutions
       not needed; two .task model files auto-download into weights/ on first run, ~13MB)
"""

from __future__ import annotations
import math
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np

from perceive import Detection

Box = Tuple[float, float, float, float]


# --------------------------------------------------------------------------- #
# the ray
# --------------------------------------------------------------------------- #
@dataclass
class GazeRay:
    origin: Tuple[float, float]        # nose tip, image px
    dir: Tuple[float, float]           # unit vector, image px coords (+x right, +y down)
    yaw: float                         # head yaw  (deg, for HUD/debug only)
    pitch: float                       # head pitch (deg)
    face_box: Box                      # bbox of the face landmarks (to match a person det)
    fwd: Optional[Tuple[float, float, float]] = None  # 3D face-forward, CAMERA coords (for eye-contact)
    pos: Optional[Tuple[float, float, float]] = None  # 3D head position (solvePnP tvec, model-mm)
    score: float = 1.0
    coarse: bool = False               # True = from Pose head landmarks (distance fallback, no 3D)

    def point_at(self, t: float) -> Tuple[float, float]:
        return (self.origin[0] + t * self.dir[0], self.origin[1] + t * self.dir[1])


# --------------------------------------------------------------------------- #
# head pose -> ray  (MediaPipe FaceMesh + solvePnP, the standard 6-point recipe)
# --------------------------------------------------------------------------- #
# FaceMesh landmark indices paired with a generic 3D face model (mm, nose tip = origin).
# This is the widely used OpenCV head-pose set; the generic model is fine because we only
# need the DIRECTION, not metric accuracy.
_LM_IDS = [1, 152, 33, 263, 61, 291]   # nose tip, chin, L eye corner, R eye corner, L mouth, R mouth
_MODEL_3D = np.array([
    (0.0,    0.0,    0.0),     # nose tip
    (0.0, -330.0,  -65.0),     # chin
    (-225.0, 170.0, -135.0),   # left eye outer corner
    (225.0,  170.0, -135.0),   # right eye outer corner
    (-150.0, -150.0, -125.0),  # left mouth corner
    (150.0,  -150.0, -125.0),  # right mouth corner
], dtype=np.float64)


# tasks-API model files (legacy mp.solutions was REMOVED in newer mediapipe). Auto-downloaded
# once (~13MB total) into weights/ next to this file — same pattern as the YOLO weights.
_MODEL_URLS = {
    "face_landmarker.task":
        "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/"
        "float16/1/face_landmarker.task",
    "pose_landmarker_lite.task":
        "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/"
        "float16/1/pose_landmarker_lite.task",
}


def _ensure_model(name: str) -> str:
    import os
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "weights", name)
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        print(f"[gaze] downloading {name} (once) …")
        import urllib.request
        urllib.request.urlretrieve(_MODEL_URLS[name], path)
    return path


def _mp_vision():
    """Shared lazy import for the mediapipe tasks API."""
    import os as _os
    _os.environ.setdefault("GLOG_minloglevel", "3")   # silence clearcut/absl noise
    try:
        import mediapipe as mp
        from mediapipe.tasks import python as mp_tasks
        from mediapipe.tasks.python import vision
    except ImportError:
        raise SystemExit("gaze.py needs mediapipe (tasks API):  pip install mediapipe")
    return mp, mp_tasks, vision


class HeadPoseEstimator:
    """frame (BGR) -> List[GazeRay].  Mediapipe tasks API (FaceLandmarker) — works on
    CURRENT mediapipe; landmark indices are identical to the old FaceMesh, so the solvePnP
    recipe below is unchanged. VIDEO mode with a synthetic monotonic clock (the timestamps
    only need to increase; real wall-time gaps from the scanning rig are irrelevant)."""

    def __init__(self, max_faces: int = 4, min_conf: float = 0.5):
        mp, mp_tasks, vision = _mp_vision()
        self._mp = mp
        self._lm = vision.FaceLandmarker.create_from_options(vision.FaceLandmarkerOptions(
            base_options=mp_tasks.BaseOptions(model_asset_path=_ensure_model("face_landmarker.task")),
            running_mode=vision.RunningMode.VIDEO,
            num_faces=max_faces,
            min_face_detection_confidence=0.3,        # lower -> keeps small/distant faces longer
            min_face_presence_confidence=0.3,
            min_tracking_confidence=0.3))
        self._ts = 0

    def estimate(self, frame_bgr: np.ndarray) -> List[GazeRay]:
        import cv2
        H, W = frame_bgr.shape[:2]
        img = self._mp.Image(image_format=self._mp.ImageFormat.SRGB,
                             data=cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB))
        self._ts += 33                              # synthetic ms clock, must be monotonic
        res = self._lm.detect_for_video(img, self._ts)
        if not res.face_landmarks:
            return []
        # pinhole intrinsics guess: focal ≈ frame width. Good enough for direction.
        cam = np.array([[W, 0, W / 2], [0, W, H / 2], [0, 0, 1]], dtype=np.float64)
        dist = np.zeros((4, 1))
        rays: List[GazeRay] = []
        for face in res.face_landmarks:             # List[List[NormalizedLandmark]]
            pts = np.array([(face[i].x * W, face[i].y * H)
                            for i in _LM_IDS], dtype=np.float64)
            ok, rvec, tvec = cv2.solvePnP(_MODEL_3D, pts, cam, dist,
                                          flags=cv2.SOLVEPNP_ITERATIVE)
            if not ok:
                continue
            # project a point 1m "out of the nose" (model -z is out of the face... the sign
            # is absorbed by solvePnP; (0,0,1000) projected from the nose gives the facing
            # line — the standard trick). Ray = nose2d -> projected point.
            end, _ = cv2.projectPoints(np.array([(0.0, 0.0, 1000.0)]), rvec, tvec, cam, dist)
            nose = pts[0]
            d = end[0][0] - nose
            n = np.hypot(d[0], d[1])
            if n < 1e-6:                    # looking straight into the camera: no 2D direction
                continue
            d = d / n
            R, _ = cv2.Rodrigues(rvec)
            pitch, yaw, _roll = cv2.RQDecomp3x3(R)[0]
            fwd = R @ np.array([0.0, 0.0, 1.0])        # model +z = out of the face (see _MODEL_3D)
            xs = [lm.x * W for lm in face]
            ys = [lm.y * H for lm in face]
            rays.append(GazeRay(origin=(float(nose[0]), float(nose[1])),
                                dir=(float(d[0]), float(d[1])),
                                yaw=float(yaw), pitch=float(pitch),
                                face_box=(min(xs), min(ys), max(xs), max(ys)),
                                fwd=tuple(float(v) for v in fwd),
                                pos=tuple(float(v) for v in tvec.flatten())))
        return rays

    def close(self):
        self._lm.close()


# --------------------------------------------------------------------------- #
# relation tests (pure 2D geometry — no models, no depth)
# --------------------------------------------------------------------------- #
def _ray_box_intersect(o: Tuple[float, float], d: Tuple[float, float], box: Box) -> Optional[float]:
    """Slab test for a HALF-line o + t*d (t>=0) vs an axis-aligned box.
    Returns the entry distance t (px) on hit, None on miss."""
    tmin, tmax = 0.0, float("inf")
    for oc, dc, lo, hi in ((o[0], d[0], box[0], box[2]), (o[1], d[1], box[1], box[3])):
        if abs(dc) < 1e-9:
            if oc < lo or oc > hi:
                return None
        else:
            t1, t2 = (lo - oc) / dc, (hi - oc) / dc
            if t1 > t2:
                t1, t2 = t2, t1
            tmin, tmax = max(tmin, t1), min(tmax, t2)
            if tmin > tmax:
                return None
    return tmin


def ray_hits_box(ray: GazeRay, box: Box, tol_deg: float = 12.0) -> Optional[dict]:
    """Does the gaze ray hit this box?  Exact half-line/box intersection, OR the box centre
    within tol_deg of the ray direction (forgiveness for a coarse head-pose ray at distance).
    Returns {'angle': deg off-axis, 'dist': px along ray} or None.  tol_deg is THE knob:
    head pose is a ±10-15° proxy for gaze, so default 12°."""
    ox, oy = ray.origin
    if box[0] <= ox <= box[2] and box[1] <= oy <= box[3]:
        return None                                    # ray starts inside the box: that's "self"
    t = _ray_box_intersect(ray.origin, ray.dir, box)
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    vx, vy = cx - ox, cy - oy
    dist = math.hypot(vx, vy)
    if dist < 1e-6:
        return None
    cosang = (vx * ray.dir[0] + vy * ray.dir[1]) / dist
    angle = math.degrees(math.acos(max(-1.0, min(1.0, cosang))))
    if t is not None:
        return {"angle": angle, "dist": t}
    if angle <= tol_deg:
        return {"angle": angle, "dist": dist}
    return None


def attach_to_persons(rays: List[GazeRay], dets: List[Detection],
                      person_labels=("person",)) -> List[Optional[int]]:
    """Bind each ray to its GAZER: the person Detection whose box contains the ray origin
    (smallest such box if several overlap — the tightest fit is the actual person).
    Returns one det index (or None) per ray. None happens when YOLO missed the person or
    only the face is in frame; the ray is still usable, with face_box as the subject proxy."""
    out: List[Optional[int]] = []
    for ray in rays:
        ox, oy = ray.origin
        best, best_area = None, float("inf")
        for j, det in enumerate(dets):
            if det.label not in person_labels:
                continue
            if det.box[0] <= ox <= det.box[2] and det.box[1] <= oy <= det.box[3] \
                    and det.area < best_area:
                best, best_area = j, det.area
        out.append(best)
    return out


def gazing_at(rays: List[GazeRay], dets: List[Detection],
              tol_deg: float = 12.0, skip_labels=("person",)) -> List[dict]:
    """Relation #5: per ray, the best detection it looks at (smallest off-axis angle,
    nearest along the ray as tie-break). A person's OWN box is excluded (the box that
    contains the ray origin); other people remain valid targets (looking AT someone).
    skip_labels filters target types if needed (default: skip person->person for now,
    eye-contact is its own relation in the template table; pass () to allow it).
    Returns [{'ray': i, 'subject': person det idx | None, 'det': j, 'angle': deg,
    'dist': px}] — one best target per ray. 'subject' + 'det' are exactly the A and B
    slots of the (A, gazing_at, B) edge for the gate/scene graph."""
    subjects = attach_to_persons(rays, dets)
    out = []
    for i, ray in enumerate(rays):
        best = None
        for j, det in enumerate(dets):
            if det.label in skip_labels:
                continue
            if j == subjects[i]:
                continue                               # the gazer's own body box
            if det.box[0] <= ray.origin[0] <= det.box[2] and \
               det.box[1] <= ray.origin[1] <= det.box[3]:
                continue                               # any other box swallowing the face
            hit = ray_hits_box(ray, det.box, tol_deg)
            if hit and (best is None or (hit["angle"], hit["dist"]) <
                        (best["angle"], best["dist"])):
                best = {"ray": i, "subject": subjects[i], "det": j, **hit}
        if best:
            out.append(best)
    return out


def eye_contact(ray: GazeRay, tol_deg: float = 12.0) -> Optional[dict]:
    """Relation: looking AT THE ROBOT. A 3D test, deliberately NOT the 2D ray — when a face
    looks straight into the camera the 2D ray degenerates (no in-plane direction), but the
    3D face-forward vs the face->camera direction is exactly well-defined there.
    Media Equation: being looked at is the social cue people read most strongly; the robot
    noticing it (and nodding back) is the cheapest legible 'social actor' response we have.
    Returns {'angle': deg off mutual gaze} or None."""
    if ray.fwd is None or ray.pos is None:
        return None
    f = np.asarray(ray.fwd)
    p = np.asarray(ray.pos)
    n = float(np.linalg.norm(p))
    if n < 1e-6:
        return None
    cosang = float(np.dot(f, -p / n))
    angle = math.degrees(math.acos(max(-1.0, min(1.0, cosang))))
    return {"angle": angle} if angle <= tol_deg else None


# Pose-landmark indices for the head (BlazePose): nose, eyes, ears.
_HEAD_LMS = {"nose": 0, "eye_l": 2, "eye_r": 5, "ear_l": 7, "ear_r": 8}


def pose_head_ray(pose, min_vis: float = 0.3) -> Optional[GazeRay]:
    """DISTANCE FALLBACK: a coarse head-orientation ray from Pose head landmarks (nose + ears),
    for when FaceMesh loses a small/far face. Pose detects heads much farther than FaceMesh.
    Direction ≈ nose relative to the ear midpoint (cheap, mostly yaw — enough for gazing-at /
    joint-attention, which are mainly horizontal). No 3D, so eye_contact skips these."""
    nose, el, er = pose[0], pose[7], pose[8]
    if nose[2] < min_vis or (el[2] < min_vis and er[2] < min_vis):
        return None
    # ear midpoint from whichever ears are visible
    ears = [e for e in (el, er) if e[2] >= min_vis]
    mx = sum(e[0] for e in ears) / len(ears); my = sum(e[1] for e in ears) / len(ears)
    dx, dy = nose[0] - mx, nose[1] - my
    n = math.hypot(dx, dy)
    if n < 1e-6:
        return None
    dx, dy = dx / n, dy / n
    head_w = abs(el[0] - er[0]) if (el[2] >= min_vis and er[2] >= min_vis) else 40.0
    hw = max(head_w, 30.0)
    box = (nose[0] - hw, nose[1] - hw, nose[0] + hw, nose[1] + hw)
    yaw = math.degrees(math.atan2(dx, max(1e-6, abs(dy)) if dy >= 0 else -abs(dy)))
    return GazeRay(origin=(float(nose[0]), float(nose[1])), dir=(float(dx), float(dy)),
                   yaw=float(yaw), pitch=0.0, face_box=box, fwd=None, pos=None, coarse=True)


# --------------------------------------------------------------------------- #
# relation #4: reaching / pointing  (arm ray — second DIRECTION source)
# --------------------------------------------------------------------------- #
@dataclass
class ArmRay:
    origin: Tuple[float, float]        # wrist, image px
    dir: Tuple[float, float]           # unit elbow->wrist, image px
    side: str                          # "left" | "right"
    extension: float                   # elbow angle (deg; 180 = dead straight)
    score: float = 1.0

    def point_at(self, t: float) -> Tuple[float, float]:
        return (self.origin[0] + t * self.dir[0], self.origin[1] + t * self.dir[1])


def arm_ray_from_points(shoulder: Tuple[float, float], elbow: Tuple[float, float],
                        wrist: Tuple[float, float], side: str = "?",
                        min_extension_deg: float = 140.0) -> Optional[ArmRay]:
    """Pure geometry (unit-testable): shoulder/elbow/wrist px -> ArmRay or None.
    The EXTENSION test is the trigger filter: a resting/bent arm emits NO ray — only a
    near-straight arm (elbow angle >= min_extension_deg) reads as reaching/pointing.
    That single threshold is what keeps #4 from firing on every idle person."""
    v1 = (shoulder[0] - elbow[0], shoulder[1] - elbow[1])   # elbow->shoulder
    v2 = (wrist[0] - elbow[0], wrist[1] - elbow[1])         # elbow->wrist
    n1, n2 = math.hypot(*v1), math.hypot(*v2)
    if n1 < 1e-6 or n2 < 1e-6:
        return None
    cosang = (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)
    ang = math.degrees(math.acos(max(-1.0, min(1.0, cosang))))
    if ang < min_extension_deg:
        return None                                          # bent arm: not pointing
    return ArmRay(origin=(float(wrist[0]), float(wrist[1])),
                  dir=(v2[0] / n2, v2[1] / n2), side=side, extension=ang)


_ARM_LMS = {"left": (11, 13, 15), "right": (12, 14, 16)}     # shoulder, elbow, wrist

# MediaPipe BlazePose 33-landmark skeleton edges (face simplified) — for the --skeleton overlay.
POSE_CONNECTIONS = [
    (0, 2), (2, 7), (0, 5), (5, 8),                          # eyes -> ears
    (9, 10),                                                  # mouth
    (11, 12), (11, 23), (12, 24), (23, 24),                  # torso
    (11, 13), (13, 15), (15, 17), (15, 19), (15, 21),        # left arm + hand
    (12, 14), (14, 16), (16, 18), (16, 20), (16, 22),        # right arm + hand
    (23, 25), (25, 27), (27, 29), (27, 31),                  # left leg + foot
    (24, 26), (26, 28), (28, 30), (28, 32),                  # right leg + foot
]
# distinct colors per detected person, so two skeletons on ONE body show as two colors.
PERSON_COLORS = [(0, 255, 0), (0, 180, 255), (255, 0, 255), (0, 255, 255),
                 (255, 128, 0), (180, 0, 255)]


def draw_pose_skeleton(img, pose, color, min_vis=0.3, thick=9):
    """Draw one person's full MediaPipe skeleton: pose = [(x_px,y_px,vis)x33]."""
    import cv2
    jr = max(3, thick // 2)                          # joint dot scales with line weight
    for a, b in POSE_CONNECTIONS:
        if a < len(pose) and b < len(pose) and pose[a][2] >= min_vis and pose[b][2] >= min_vis:
            cv2.line(img, (int(pose[a][0]), int(pose[a][1])),
                     (int(pose[b][0]), int(pose[b][1])), color, thick, cv2.LINE_AA)
    for x, y, v in pose:
        if v >= min_vis:
            cv2.circle(img, (int(x), int(y)), jr, color, -1, cv2.LINE_AA)


class ArmRayEstimator:
    """frame (BGR) -> List[ArmRay].  Tasks-API PoseLandmarker, num_poses>1 — MULTI-person
    (the legacy single-person limit is gone): every extended arm in frame gets a ray, which
    is what two-person pointing / 'offering' scenarios need."""

    def __init__(self, max_people: int = 4, min_conf: float = 0.5,
                 min_extension_deg: float = 140.0, min_visibility: float = 0.5,
                 smooth: float = 0.5):
        mp, mp_tasks, vision = _mp_vision()
        self._mp = mp
        self._lm = vision.PoseLandmarker.create_from_options(vision.PoseLandmarkerOptions(
            base_options=mp_tasks.BaseOptions(model_asset_path=_ensure_model("pose_landmarker_lite.task")),
            running_mode=vision.RunningMode.VIDEO,
            num_poses=max_people,
            min_pose_detection_confidence=min_conf,
            min_pose_presence_confidence=0.3,   # lower -> stops the person flickering OUT when still
            min_tracking_confidence=0.3))       # lower -> keeps the track alive between detections
        self._ts = 0
        self.min_extension_deg = min_extension_deg
        self.min_visibility = min_visibility
        self.smooth = smooth                    # EMA factor: new = smooth*new + (1-smooth)*prev (lower=smoother)
        self._prev: List[list] = []             # previous smoothed poses, for temporal smoothing
        self.last_debug: List[dict] = []   # per shoulder/elbow/wrist (for --skeleton): pts, vis, angle
        self.last_poses: List[list] = []   # full per-person landmark skeletons (for --skeleton)

    def estimate(self, frame_bgr: np.ndarray) -> List[ArmRay]:
        import cv2
        H, W = frame_bgr.shape[:2]
        img = self._mp.Image(image_format=self._mp.ImageFormat.SRGB,
                             data=cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB))
        self._ts += 33
        res = self._lm.detect_for_video(img, self._ts)
        # raw poses in pixels: per person [(x,y,vis) x 33]
        raw = [[(lm.x * W, lm.y * H, getattr(lm, "visibility", 1.0) or 1.0) for lm in person]
               for person in (res.pose_landmarks or [])]
        # TEMPORAL SMOOTHING (EMA): a still person's landmarks stop jittering frame to frame.
        # matched by person index (stable for 1 person; good enough for the debug view).
        a = self.smooth
        sm = []
        for i, pose in enumerate(raw):
            if i < len(self._prev) and len(self._prev[i]) == len(pose):
                prev = self._prev[i]
                pose = [(a * x + (1 - a) * px, a * y + (1 - a) * py, v)
                        for (x, y, v), (px, py, _) in zip(pose, prev)]
            sm.append(pose)
        self._prev = sm
        self.last_poses = sm

        out: List[ArmRay] = []
        self.last_debug = []
        for pose in sm:                              # arm rays from the SMOOTHED landmarks
            for side, (s, e, w) in _ARM_LMS.items():
                vis = min(pose[s][2], pose[e][2], pose[w][2])
                sp, ep, wp = pose[s][:2], pose[e][:2], pose[w][:2]
                v1 = (sp[0] - ep[0], sp[1] - ep[1]); v2 = (wp[0] - ep[0], wp[1] - ep[1])
                n1 = math.hypot(*v1); n2 = math.hypot(*v2)
                ang = (math.degrees(math.acos(max(-1.0, min(1.0,
                       (v1[0]*v2[0] + v1[1]*v2[1]) / (n1*n2)))))) if n1 > 0 and n2 > 0 else 0.0
                self.last_debug.append({"side": side, "pts": (sp, ep, wp), "vis": vis, "angle": ang})
                if vis < self.min_visibility:
                    continue
                r = arm_ray_from_points(sp, ep, wp, side=side,
                                        min_extension_deg=self.min_extension_deg)
                if r:
                    out.append(r)
        return out

    def close(self):
        self._lm.close()


def pointing_at(arm_rays: List[ArmRay], dets: List[Detection],
                tol_deg: float = 15.0, skip_labels=()) -> List[dict]:
    """Relation #4 (reaching/pointing-at): the SAME test as gazing_at — ray_hits_box is
    duck-typed on .origin/.dir, so arm rays go through the identical machinery. Defaults
    differ: pointing AT a person is meaningful (reaching toward someone), so skip_labels=();
    tolerance a touch wider (an extended arm under-points at distant targets)."""
    return gazing_at(arm_rays, dets, tol_deg=tol_deg, skip_labels=skip_labels)


def joint_attention(rays: List[GazeRay], image_wh: Tuple[int, int],
                    margin: float = 0.25) -> List[dict]:
    """Relation #6: do ≥2 gaze rays CONVERGE? For each pair, intersect the two half-lines;
    a convergence point must lie IN FRONT of both faces (t>0) and inside the frame
    (± margin·frame, so a target slightly out of view still counts).
    Returns [{'rays': (i,j), 'point': (x,y)}]. Stronger signal than a single gazing-at."""
    W, H = image_wh
    out = []
    for i in range(len(rays)):
        for j in range(i + 1, len(rays)):
            o1, d1 = rays[i].origin, rays[i].dir
            o2, d2 = rays[j].origin, rays[j].dir
            denom = d1[0] * d2[1] - d1[1] * d2[0]      # cross(d1, d2)
            if abs(denom) < 1e-9:
                continue                               # parallel: no convergence
            wx, wy = o2[0] - o1[0], o2[1] - o1[1]
            t1 = (wx * d2[1] - wy * d2[0]) / denom
            t2 = (wx * d1[1] - wy * d1[0]) / denom
            if t1 <= 0 or t2 <= 0:
                continue                               # behind one of the faces: divergent
            px, py = o1[0] + t1 * d1[0], o1[1] + t1 * d1[1]
            if -margin * W <= px <= (1 + margin) * W and -margin * H <= py <= (1 + margin) * H:
                out.append({"rays": (i, j), "point": (px, py)})
    return out


# --------------------------------------------------------------------------- #
# overlay helpers — solid palette-coloured strokes, NOTHING layered on top: a
# single stroke in the EXACT palette colour (no dim halo, no lightened hot core),
# so a thick line keeps the true colour-card hue instead of washing out. Text
# keeps a thin dark outline only because glyphs are otherwise unreadable over
# video clutter. Module-level so the demos reuse the same look.
# --------------------------------------------------------------------------- #
def draw_text(img, txt, org, color, scale=0.9, thick=2):
    import cv2
    x, y = int(org[0]), int(org[1])
    cv2.putText(img, txt, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale, _BG,
                thick + 4, cv2.LINE_AA)
    cv2.putText(img, txt, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale, color,
                thick, cv2.LINE_AA)


def draw_box(img, box, color, thick=4):
    import cv2
    x1, y1, x2, y2 = map(int, box)
    cv2.rectangle(img, (x1, y1), (x2, y2), color, thick)


def draw_arrow(img, p1, p2, color, thick=4):
    import cv2
    p1 = (int(p1[0]), int(p1[1])); p2 = (int(p2[0]), int(p2[1]))
    cv2.arrowedLine(img, p1, p2, color, thick, cv2.LINE_AA, tipLength=0.04)


def draw_circle(img, c, radius, color, thick=4):
    import cv2
    c = (int(c[0]), int(c[1]))
    cv2.circle(img, c, radius, color, thick, cv2.LINE_AA)


def draw_panel(img, lines, org=(16, 16), scale=0.85):
    """Translucent black panel (the TOTAL-SCORE look) listing active relations.
    lines = [(text, BGR color), ...]; draws nothing when empty."""
    import cv2
    if not lines:
        return
    pad, lh = 14, int(34 * scale + 10)
    w = max(cv2.getTextSize(t, cv2.FONT_HERSHEY_SIMPLEX, scale, 2)[0][0] for t, _ in lines)
    x, y = org
    over = img.copy()
    cv2.rectangle(over, (x, y), (x + w + 2 * pad, y + lh * len(lines) + pad), _BG, -1)
    cv2.addWeighted(over, 0.55, img, 0.45, 0, img)
    for k, (t, col) in enumerate(lines):
        draw_text(img, t, (x + pad, y + pad + lh * k + int(22 * scale)), col, scale)


# overlay palette (BGR) — mirrors the web UI palette:
#   #A1CC48 light green · #D9E157 yellow-green · #D95B5B red · #E89D9D light red
#   · #88E4EA blue · #262626 near-black background
_BG = (38, 38, 38)                       # #262626 — text outline / panel fill
C_GREEN  = (72, 204, 161)                # #A1CC48 light green — person / structure / "noticed"
C_RED    = (91, 91, 217)                 # #D95B5B red — satisfied / veto
C_YELLOW = (87, 225, 217)                # #D9E157 yellow-green — a relation detected (pointing)
C_ORANGE = (157, 157, 232)               # #E89D9D light red — cooling
C_MAGENTA = (91, 91, 217)                # #D95B5B red — joint-attention moment circle
C_CYAN   = (234, 228, 136)               # #88E4EA blue — attention rays / logic
C_WHITE  = (228, 232, 232)               # #E8E8E4 near-white — neutral text


# --------------------------------------------------------------------------- #
# live test:  python gaze.py [--camera 0 | --camera http://<ip>/] [--detect]
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import argparse, time
    import cv2

    ap = argparse.ArgumentParser(description="attention-relations live test: gazing-at (#5), "
                                             "joint-attention (#6), pointing-at (#4), eye-contact")
    ap.add_argument("--camera", default="0", help="webcam index (0) or M5 MJPEG URL")
    ap.add_argument("--tol", type=float, default=12.0, help="gazing-at angular tolerance (deg)")
    ap.add_argument("--no-arms", action="store_true", help="skip MediaPipe Pose (#4 pointing)")
    ap.add_argument("--skeleton", action="store_true",
                    help="debug: draw shoulder-elbow-wrist + elbow angle/visibility (see why no arm ray)")
    ap.add_argument("--detect", action="store_true",
                    help="also run a detector and highlight gazed-at / pointed-at objects")
    ap.add_argument("--detector", default="yolo", help="yolo | yoloworld | gdino (with --detect)")
    ap.add_argument("--vocab", default="person,laptop,monitor,keyboard,cup,bottle,chair,desk,"
                                       "bag,potted plant,book,phone")
    ap.add_argument("--conf", type=float, default=0.3)
    args = ap.parse_args()

    src = int(args.camera) if args.camera.isdigit() else args.camera
    cap = cv2.VideoCapture(src)
    if not cap.isOpened():
        raise SystemExit(f"cannot open camera {args.camera!r} "
                         "(M5: ONE viewer at a time — close other streams)")

    est = HeadPoseEstimator()
    arm_est = None if args.no_arms else ArmRayEstimator()
    det = None
    if args.detect:
        from perceive import make_detector
        det = make_detector(args.detector, [v.strip() for v in args.vocab.split(",")], conf=args.conf)

    print("q to quit.  yellow = gaze ray, orange = arm ray, cyan = gazer (subject), "
          "green = gazed-at / pointed-at object, magenta ● = joint attention, "
          "EYE CONTACT flashes on the face box")
    t0, nf = time.time(), 0
    while True:
        ok, fr = cap.read()
        if not ok:
            time.sleep(0.05); continue
        H, W = fr.shape[:2]
        rays = est.estimate(fr)
        arms = arm_est.estimate(fr) if arm_est else []
        dets = det.detect(fr) if det else []

        # DISTANCE FALLBACK (B): FaceMesh drops small/far faces; add a coarse Pose head ray for any
        # person the FaceMesh didn't cover. Near faces still use the precise FaceMesh ray (A).
        if arm_est:
            for pose in arm_est.last_poses:
                hr = pose_head_ray(pose)
                if hr is None:
                    continue
                thr = max(50.0, 0.5 * (hr.face_box[2] - hr.face_box[0]))
                if any((not r.coarse) and math.hypot(r.origin[0] - hr.origin[0],
                        r.origin[1] - hr.origin[1]) < thr for r in rays):
                    continue                       # a FaceMesh ray already covers this head
                rays.append(hr)

        panel = []                                              # active relations -> HUD panel

        for d in dets:                                          # detector boxes (thin, background)
            x1, y1, x2, y2 = map(int, d.box)
            cv2.rectangle(fr, (x1, y1), (x2, y2), (160, 160, 160), 1)
            draw_text(fr, d.label, (x1, y1 - 6), (200, 200, 200), 0.55, 1)

        if args.skeleton and arm_est:                           # debug: FULL MediaPipe skeleton per person
            for pid, pose in enumerate(arm_est.last_poses):     # each person a distinct color
                col = PERSON_COLORS[pid % len(PERSON_COLORS)]   # -> two skeletons on one body = two colors
                draw_pose_skeleton(fr, pose, col)
                if pose and pose[0][2] >= 0.3:                  # label person index near the nose
                    draw_text(fr, f"P{pid}", (int(pose[0][0]) + 6, int(pose[0][1]) - 6), col, 0.6, 2)
            draw_text(fr, f"MediaPipe poses: {len(arm_est.last_poses)}", (12, 30), C_WHITE, 0.7, 2)

        for a in arms:                                          # arm rays (orange, thick)
            draw_arrow(fr, a.origin, a.point_at(0.5 * math.hypot(W, H)), C_ORANGE)
            draw_text(fr, f"{a.extension:.0f}", (a.origin[0] + 8, a.origin[1] + 24),
                      C_ORANGE, 0.7)

        for r in rays:                                          # gaze rays: yellow=FaceMesh, blue=far fallback
            col = (255, 200, 90) if r.coarse else C_YELLOW
            draw_arrow(fr, r.origin, r.point_at(0.6 * math.hypot(W, H)), col)
            tag = "head~" if r.coarse else f"y{r.yaw:+.0f} p{r.pitch:+.0f}"
            draw_text(fr, tag, (r.origin[0] + 10, r.origin[1] - 12), col, 0.55, 1)

        hits = gazing_at(rays, dets, tol_deg=args.tol) if dets else []
        for h in hits:                                          # relation #5: subject -> target
            d = dets[h["det"]]
            draw_box(fr, d.box, C_GREEN)
            draw_text(fr, d.label.upper(), (d.box[0], d.box[3] + 30), C_GREEN)
            sb = dets[h["subject"]].box if h["subject"] is not None else rays[h["ray"]].face_box
            draw_box(fr, sb, C_CYAN, 3)
            panel.append((f"GAZING-AT  {d.label}  {h['angle']:.0f}deg", C_GREEN))

        phits = pointing_at(arms, dets) if dets else []
        for h in phits:                                         # relation #4: pointed-at target
            d = dets[h["det"]]
            draw_box(fr, d.box, C_GREEN)
            draw_text(fr, d.label.upper(), (d.box[0], d.box[1] - 12), C_GREEN)
            panel.append((f"POINTING-AT  {d.label}  {h['angle']:.0f}deg", C_ORANGE))

        for r in rays:                                          # eye contact (white, loud)
            ec = eye_contact(r)
            if ec:
                draw_box(fr, r.face_box, C_WHITE)
                draw_text(fr, "EYE CONTACT", (r.face_box[0], r.face_box[1] - 14), C_WHITE)
                panel.append((f"EYE CONTACT  {ec['angle']:.0f}deg", C_WHITE))

        for c in joint_attention(rays, (W, H)):                 # relation #6: convergence
            draw_circle(fr, c["point"], 22, C_MAGENTA)
            draw_text(fr, "JOINT ATTENTION", (c["point"][0] + 30, c["point"][1] + 8), C_MAGENTA)
            panel.append(("JOINT ATTENTION", C_MAGENTA))

        draw_panel(fr, panel)                                   # TOTAL-SCORE-style status panel
        nf += 1
        draw_text(fr, f"{len(rays)} face  {len(arms)} arm  {nf/(time.time()-t0):.1f} fps",
                  (12, H - 16), C_WHITE, 0.65, 2)
        cv2.imshow("gaze ray test", fr)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break
    cap.release(); cv2.destroyAllWindows(); est.close()
    if arm_est:
        arm_est.close()
