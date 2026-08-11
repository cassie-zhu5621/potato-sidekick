"""A pose has to look like a body before it may be counted as one.

PoseLandmarker fits its 33 points to whatever is in front of it -- a chair back,
the vertical of a door frame -- and this rig deliberately lowers the presence and
tracking confidences to 0.3 so that a STILL person stops flickering out. That
trade bought stability for real people and let phantoms in with them.

The cost was never the false pose. It was that `gathering` counts people without
asking whether they look like any: a phantom holding steady sets the stable count
to 1, so a real arrival does not CHANGE it, and standing in the doorway reports
nothing.

THE TEST HAS BEEN WRONG TWICE, in opposite directions, and both are recorded here
because each was introduced while fixing the other.

  1. Shoulders AND hips averaged -- 2026-08-08. Someone a metre from the lens has
     no hips in frame, MediaPipe reports them at low confidence, the average fell
     under the threshold, and a real person was discarded. She was dropped and
     re-found as she shifted, the count flickered, and `gathering` fired on it:
     the exact failure the filter existed to prevent, caused by the filter.

  2. Shoulder width alone -- 2026-08-09. Shoulder width COLLAPSES WHEN A PERSON
     TURNS, and turning is what reaching sideways for something does. Five frames
     of one person at one distance picking up a plant:

         frame   shoulder  nose-neck  sh-elbow  sh-hip
           0        12         94        133      231
           1         9         96        134      232
           2        11         96        132      230
           3        48         97        141      242
           4        99        119        157      257

     Frames 0-2 are her in profile with a hand on the leaves; all three were
     discarded, so relation 9 had no pose to test and "touch the plant" could not
     be reported at all.

So the size test now takes the LARGEST of several segments, and a pose that fails
it is MARKED rather than dropped -- because "is there another person here" and
"is that hand on the plant" want different evidence, and only the first is a claim
about a body.
"""
from __future__ import annotations

import inspect
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from perception.relations import (L_EL, L_HIP, L_SH, NOSE, R_EL, R_HIP, R_SH,
                                  PoseEstimator, RelationEngine, _NEEDED)

W = 1280


def _raw(shoulder_px=200, shoulder_vis=0.95, hip_vis=0.9, torso_px=200,
         arm_px=120, neck_px=90, n=33):
    """A landmark array as PoseLandmarker returns it, scaled to pixels."""
    pose = [(640.0, 360.0, 0.9)] * n
    half = shoulder_px / 2.0
    pose[L_SH] = (640 - half, 300.0, shoulder_vis)
    pose[R_SH] = (640 + half, 300.0, shoulder_vis)
    pose[L_HIP] = (640 - half * 0.8, 300.0 + torso_px, hip_vis)
    pose[R_HIP] = (640 + half * 0.8, 300.0 + torso_px, hip_vis)
    pose[L_EL] = (640 - half, 300.0 + arm_px, 0.9)
    pose[R_EL] = (640 + half, 300.0 + arm_px, 0.9)
    pose[NOSE] = (640.0, 300.0 - neck_px, 0.9)
    return pose


def _pts(raw):
    return {i: (raw[i][0], raw[i][1]) for i in _NEEDED}


def _solid(raw):
    return PoseEstimator.body_size(_pts(raw)) >= PoseEstimator.MIN_BODY_FRAC * W


# --------------------------------------------------------- what counts as a body --
def test_a_real_body_is_solid():
    assert _solid(_raw())


def test_a_person_in_profile_is_solid():
    """THE 2026-08-09 REPORT, as numbers. Shoulders nine pixels apart because she
    is side-on, everything else full size. This is the posture of reaching for
    something, so it is the posture relation 9 exists to see."""
    assert _solid(_raw(shoulder_px=9, torso_px=232, arm_px=134, neck_px=96))


def test_a_person_close_enough_to_crop_the_hips_is_solid():
    """THE 2026-08-08 REGRESSION. No hips in frame; the shoulders carry it."""
    assert _solid(_raw(shoulder_px=380, hip_vis=0.05, torso_px=4))


def test_a_fit_to_texture_is_not_solid():
    """A phantom is small in EVERY segment at once -- which is the only thing
    that reliably separates it, and the reason the test is a max rather than any
    one measurement."""
    assert not _solid(_raw(shoulder_px=20, torso_px=30, arm_px=18, neck_px=14))


def test_no_single_segment_can_veto():
    """Each one has a posture that flattens it: shoulders by yaw, torso by
    standing close, neck by looking down. Taking the max means a bad reading can
    only fail to help."""
    for kw in ({"shoulder_px": 9}, {"torso_px": 3}, {"neck_px": 2}, {"arm_px": 2}):
        assert _solid(_raw(**kw)), kw


def test_the_visibility_gate_still_drops_outright():
    """Confidence is different from size: the model saying it does not believe
    its own shoulders is not a posture, it is a non-answer."""
    src = inspect.getsource(PoseEstimator.estimate)
    assert "MIN_SHOULDER_VIS" in src
    assert "continue" in src.split("for raw in sm:")[1]


def test_the_threshold_is_not_so_tight_it_excludes_people():
    """Guards the other direction. Calibrated against the recorded sessions:
    real people sat at a median 26% of frame width, p10 at 14.5%."""
    assert PoseEstimator.MIN_SHOULDER_VIS <= 0.7
    assert PoseEstimator.MIN_BODY_FRAC <= 0.15, PoseEstimator.MIN_BODY_FRAC


# ------------------------------------------- who consults it, and who must not --
def test_a_pose_that_fails_the_size_test_is_kept_and_marked():
    """Dropping it is what cost relation 9 everything. The flag travels; the
    pose stays."""
    src = inspect.getsource(PoseEstimator.estimate)
    assert "solid=" in src
    body = src[src.index("for raw in sm:"):]
    assert body.count("continue") == 1, "size must no longer discard a pose"


def test_gathering_counts_only_solid_bodies():
    """The claim it makes IS about a body, so a door frame must not make it."""
    src = inspect.getsource(RelationEngine.evaluate)
    i = src.index("self._count_hist.append")
    assert "len(solid)" in src[i:i + 60], src[i:i + 60]


def test_person_to_person_relations_use_solid_bodies():
    src = inspect.getsource(RelationEngine.evaluate)
    i = src.index("pairs = [")
    assert "enumerate(solid)" in src[i:i + 120]


def test_hands_on_does_not_consult_solid():
    """It is anchored by the OBJECT: a phantom does not hold a hand inside a
    specific box for sustain_s. Requiring solidity here bought nothing and cost
    every profile posture."""
    src = inspect.getsource(RelationEngine.evaluate)
    i = src.index("grace = self.touch_grace_frames")
    j = src.index("self._count_hist.append")
    assert "solid" not in src[i:j], src[i:j]
