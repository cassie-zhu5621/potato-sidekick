"""
perceive.py — STABLE perception: a frame -> a scene graph with stable relations.

This is the linchpin the rest of the pipeline stands on. The whole point (see the
discussion / FINDINGS): the GATE needs a relational encoding that is *stable* across
frames, or it can never habituate ("same situation" must map to "same graph"). VLMs and
learned SGG models do NOT give stable relations (they relabel per frame — the
scientist/researcher/student flip-flop, PE-Net's intra/inter-class problem). So we get
stability the cheap, boring, reliable way:

    off-the-shelf OBJECT detection (stable boxes)  +  GEOMETRIC relations by rule.

The VLM is NOT used here; it is the downstream judgment brain. Object identity comes from
a detector (mature, consistent); relations come from geometry (deterministic), not from a
model guessing predicates.

ROTATION NOTE (found in the real lab frames): the camera sits on a pan/tilt head, so frames
arrive tilted/sideways. "on / above / below" assume a known up-direction and become
unreliable when the view is rotated. So relations split into:
  - rotation-INVARIANT (always safe): near, touching/overlapping, inside/contains
  - direction (needs an up-vector): on, above, below, left_of, right_of
The pan/tilt pose can supply `up`; without it we emit only the invariant relations.

The detector is a pluggable ADAPTER. The default is a MockDetector for testing the graph
logic here; on the rig you drop in YOLO-World (open-vocab, no hand-listed objects — matches
the brief) / Grounding DINO / YOLOv8. See `YoloWorldDetector` stub at the bottom.
"""

from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import Any, List, Tuple, Optional, Dict, Protocol

Box = Tuple[float, float, float, float]   # x1, y1, x2, y2 in pixels


# --------------------------------------------------------------------------- #
# detections + detector adapter
# --------------------------------------------------------------------------- #
@dataclass
class Detection:
    label: str            # object TYPE (e.g. "laptop"); becomes the node type
    box: Box
    score: float = 1.0

    @property
    def cx(self): return 0.5 * (self.box[0] + self.box[2])
    @property
    def cy(self): return 0.5 * (self.box[1] + self.box[3])
    @property
    def w(self):  return self.box[2] - self.box[0]
    @property
    def h(self):  return self.box[3] - self.box[1]
    @property
    def area(self): return max(self.w, 0) * max(self.h, 0)


class Detector(Protocol):
    """Anything that turns an image into detections. Swap implementations freely."""
    def detect(self, image) -> List[Detection]: ...


# --------------------------------------------------------------------------- #
# geometry helpers
# --------------------------------------------------------------------------- #
def _iou(a: Box, b: Box) -> float:
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    ua = (a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def _frac_inside(inner: Box, outer: Box) -> float:
    ix1, iy1 = max(inner[0], outer[0]), max(inner[1], outer[1])
    ix2, iy2 = min(inner[2], outer[2]), min(inner[3], outer[3])
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inner_area = (inner[2]-inner[0]) * (inner[3]-inner[1])
    return (iw * ih) / inner_area if inner_area > 0 else 0.0


# --------------------------------------------------------------------------- #
# the scene graph builder
# --------------------------------------------------------------------------- #
@dataclass
class SceneGraph:
    nodes: Dict[str, str]               # node_id -> type    (config_gate format)
    edges: List[Tuple[str, str, str]]   # (src_id, relation, dst_id)
    boxes: Dict[str, Box] = field(default_factory=dict)   # node_id -> box (for salience/viz)

    def as_gate_input(self):
        return self.nodes, self.edges


# Movers/agents are never a SURFACE or a CONTAINER — a person carries/sits, they don't hold
# things "inside" or serve as a surface. Without depth, a big foreground person's box swallows
# everything behind it -> false "X inside/on person"; blacklisting these types kills that.
NON_CONTAINER_TYPES = {"person", "people", "man", "woman", "boy", "girl", "child",
                       "hand", "cat", "dog", "bird", "animal"}

# Only these plausibly CONTAIN other objects, so a 2D box-in-box means a real "inside". Anything
# else that merely nests in 2D — a chair box swallowing a bag, a desk swallowing a laptop, one
# chair box over another — is occlusion or stacking, NOT containment; we leave it to "on"/"near".
# In a wide room almost nothing is truly inside anything, so this whitelist kills the false-
# inside explosion while keeping the few real cases (a thing in a bag/box/cup).
CONTAINER_TYPES = {"bag", "backpack", "handbag", "suitcase", "box", "basket", "bin", "drawer",
                   "cabinet", "shelf", "bookshelf", "cup", "bowl", "pot", "jar", "container"}


def build_graph(dets: List[Detection], image_wh: Tuple[int, int],
                up: Optional[Tuple[float, float]] = None,
                near_frac: float = 0.18, min_score: float = 0.30,
                surface_frac: float = 0.33, surface_min_holds: int = 3,
                overlapping: bool = False, min_area_frac: float = 0.0,
                max_container_frac: float = 0.5,
                depth_map=None, depth_tol: float = 0.15) -> SceneGraph:
    """Detections -> stable scene graph.

    near_frac: two objects are 'near' if their centre distance < near_frac * image diagonal.
    up: unit vector of 'up' in image pixel coords (e.g. from pan/tilt pose). If given, we
        additionally emit direction relations (on/above/below/left_of/right_of); if None we
        stay rotation-invariant (near/touching/inside) so we never assert a wrong 'on'.

    SURFACE rule (data-driven, no dataset tuning): a detection that (a) covers >= surface_frac
    of the frame AND (b) has >= surface_min_holds other objects sitting inside it is treated
    as a SURFACE/background (desk, mat, wall, floor). A surface only takes 'on' edges from the
    things it holds; it does NOT spray near/touching to everything. This kills the O(N^2) hub
    that a big table/mat otherwise creates, while keeping the informative "X is on the table".
    The rule auto-disables when no such hub exists, so it is safe on any data.
    """
    W, H = image_wh
    diag = math.hypot(W, H)
    frame_area = max(W * H, 1)
    # drop low-confidence AND too-small detections. Small boxes (a distant bottle) are the
    # least stable to detect (they flicker in/out frame to frame -> phantom relation changes)
    # and the least relationally important, so for the GATE we ignore objects below
    # min_area_frac of the frame. This is a per-deployment knob: raise it in a cluttered/far
    # scene (a shared room), keep it ~0 for a close simple scene (a single desk).
    dets = [d for d in dets if d.score >= min_score and d.area >= min_area_frac * frame_area]

    # assign stable-ish ids: type + an index by reading order (left->right, top->bottom).
    dets = sorted(dets, key=lambda d: (round(d.cy / max(H, 1), 2), d.cx))
    counts: Dict[str, int] = {}
    nodes, boxes, ids = {}, {}, []
    for d in dets:
        counts[d.label] = counts.get(d.label, 0) + 1
        nid = f"{d.label}{counts[d.label]}"
        nodes[nid] = d.label
        boxes[nid] = d.box
        ids.append(nid)

    # --- identify surfaces (data-driven) ---
    is_surface = [False] * len(dets)
    for k, s in enumerate(dets):
        if s.area >= surface_frac * frame_area and s.label not in NON_CONTAINER_TYPES:
            holds = sum(1 for m, o in enumerate(dets)
                        if m != k and _frac_inside(o.box, s.box) >= 0.5 and o.area < s.area)
            if holds >= surface_min_holds:
                is_surface[k] = True

    # per-detection median depth (0..1) if a depth map was supplied (monocular Depth-Anything).
    # Two boxes are only "inside/on"-related if they are at a SIMILAR depth — a thing BEHIND a
    # big foreground object is at a different depth, so it is NOT contained/supported by it.
    # This is the principled replacement for the size-cap / person-blacklist band-aids.
    import numpy as _np
    def _bd(box):
        if depth_map is None:
            return None
        H2, W2 = depth_map.shape[:2]
        x1, y1, x2, y2 = (max(0, int(box[0])), max(0, int(box[1])),
                          min(W2, int(box[2])), min(H2, int(box[3])))
        if x2 <= x1 or y2 <= y1:
            return None
        return float(_np.median(depth_map[y1:y2, x1:x2]))
    depth_of = [_bd(d.box) for d in dets]
    def dok(i, j):                       # depth-consistent? (always True when no depth map)
        if depth_map is None:
            return True
        di, dj = depth_of[i], depth_of[j]
        return di is not None and dj is not None and abs(di - dj) <= depth_tol

    edges: List[Tuple[str, str, str]] = []
    for i in range(len(dets)):
        for j in range(len(dets)):
            if i == j:
                continue
            a, b = dets[i], dets[j]
            ai, bi = ids[i], ids[j]

            # ---- surface handling: only 'X on surface', nothing else ----
            if is_surface[i] or is_surface[j]:
                if (is_surface[j] and not is_surface[i]
                        and _frac_inside(a.box, b.box) >= 0.5 and dok(i, j)):
                    edges.append((ai, "on", bi))          # a sits on surface b (same depth)
                continue                                   # suppress all other surface edges

            # ---- rotation-INVARIANT relations (always safe) ----
            fi = _frac_inside(a.box, b.box)
            # 'inside' only if b is a WHITELISTED container type AND a is almost fully enclosed.
            # Without depth, any big box swallows boxes behind it in 2D -> false 'inside' (chair
            # in chair, laptop in desk, person in chair). Restricting to real containers + near-
            # full enclosure + different type kills that explosion; everything else stays on/near.
            if (fi >= 0.90 and a.label != b.label and b.label in CONTAINER_TYPES
                    and b.area > a.area and b.area < max_container_frac * frame_area
                    and dok(i, j)):
                edges.append((ai, "inside", bi))
                continue
            if overlapping and _iou(a.box, b.box) > 0.02 and i < j:
                # OFF by default: on real lab data 'overlapping' was ~57% of all edges and
                # mostly depth OCCLUSION (a chair in front of a desk/person), not contact —
                # it bloated the graph 2x while barely changing what the gate fired on. We
                # keep near/inside/on (the meaningful spatial structure) and let the VLM
                # supply true physical relations at report time. Re-enable with overlapping=True.
                edges.append((ai, "overlapping", bi)); edges.append((bi, "overlapping", ai))
            dist = math.hypot(a.cx - b.cx, a.cy - b.cy)
            if i < j and dist < near_frac * diag and _iou(a.box, b.box) == 0:
                edges.append((ai, "near", bi)); edges.append((bi, "near", ai))
            # ---- DIRECTION relations (only with a known up-vector) ----
            if up is not None and i < j:
                ux, uy = up
                dx, dy = a.cx - b.cx, a.cy - b.cy
                along_up = dx * ux + dy * uy
                along_right = dx * (-uy) + dy * ux
                if dist < 0.45 * diag:
                    if abs(along_up) > abs(along_right):
                        edges.append((ai, "above" if along_up > 0 else "below", bi))
                    else:
                        edges.append((ai, "right_of" if along_right > 0 else "left_of", bi))

    sg = SceneGraph(nodes=nodes, edges=edges, boxes=boxes)
    sg.surfaces = [ids[k] for k in range(len(dets)) if is_surface[k]]   # for transparency
    return sg


def perceive(image, detector: Detector, image_wh: Tuple[int, int],
             up: Optional[Tuple[float, float]] = None) -> SceneGraph:
    """Full step: image -> detections -> stable scene graph."""
    return build_graph(detector.detect(image), image_wh, up=up)


# --------------------------------------------------------------------------- #
# detector adapters
# --------------------------------------------------------------------------- #
# obviously-immovable categories: once detected at a spot, LATCH them there so the detector
# missing them for a few frames doesn't fake a "left/arrived" event. They can't actually move.
STATIC_TYPES = {"desk", "table", "chair", "bookshelf", "shelf", "cabinet", "monitor", "tv",
                "screen", "potted plant", "plant", "sofa", "couch", "bed", "lamp", "whiteboard",
                "window", "door", "refrigerator", "sink", "oven"}


class StaticLatch:
    """Stabilises detection of immovable objects. Once a static-type object is seen at a location
    it is remembered (per type + coarse cell); if the detector misses it on later frames, its last
    box is re-injected. So furniture/plants/monitors stop flickering in&out — only genuinely
    movable things (person, cup, bag, laptop...) drive add/remove events.
      latch = StaticLatch();  dets = latch.apply(det.detect(frame), (W, H))
    A latched object is dropped only if unseen for `forget` frames (it really may have been moved)."""
    def __init__(self, static=STATIC_TYPES, grid=12, forget=400):
        self.static, self.grid, self.forget = static, grid, forget
        self.latched = {}   # (type, cell) -> [Detection, last_seen_step]
        self._step = 0

    def apply(self, dets, image_wh):
        self._step += 1
        W, H = image_wh
        def cell(b): return (int((b[0]+b[2])/2 / max(W, 1) * self.grid),
                             int((b[1]+b[3])/2 / max(H, 1) * self.grid))
        present = set()
        for d in dets:
            if d.label in self.static:
                key = (d.label, cell(d.box))
                self.latched[key] = [d, self._step]
                present.add(key)
        out = list(dets)
        for key, (d, seen) in list(self.latched.items()):
            if self._step - seen > self.forget:
                del self.latched[key]
            elif key not in present:
                out.append(d)            # re-inject the missed static object at its last position
        return out


class MockDetector:
    """Returns a fixed detection list — for testing the graph logic without a model."""
    def __init__(self, dets: List[Detection]):
        self._dets = dets
    def detect(self, image=None) -> List[Detection]:
        return self._dets


class YoloWorldDetector:
    """Drop-in real detector on the rig. ~10 lines; open-vocab, no training, no object list
    baked in (you pass the vocabulary you care about, or a broad default).

        pip install ultralytics
        det = YoloWorldDetector(["person","laptop","cup","chair","desk","phone","book","plush toy"])
        graph = perceive(frame, det, (W,H), up=pose_up)
    """
    def __init__(self, vocabulary: List[str], weights: str = "yolov8s-world.pt",
                 conf: float = 0.25, device: str = None):
        try:
            from ultralytics import YOLO      # lazy import; only needed on the rig/laptop
        except ImportError:
            raise SystemExit("YOLO-World needs ultralytics. Install it:\n"
                             "    pip install ultralytics\n"
                             "(or run the batch with --mock to test the plumbing).")
        vocabulary = _normalise_vocabulary(vocabulary)
        if not vocabulary:
            raise ValueError("YOLO-World vocabulary must contain at least one label")
        self.model_name = weights
        self.model = YOLO(weights)            # auto-downloads yolov8s-world.pt on first use
        self.model.set_classes(vocabulary)
        self.vocab = vocabulary
        self.conf = conf
        self.device = device                  # None=auto, "cpu", or "0" for first GPU
    def set_vocab(self, vocabulary):          # dynamic open-vocab: planner 'detect' drives the prompt
        vocabulary = _normalise_vocabulary(vocabulary)
        if vocabulary:
            self.model.set_classes(vocabulary)
            self.vocab = vocabulary
    def detect(self, image) -> List[Detection]:
        r = self.model.predict(image, conf=self.conf, device=self.device, verbose=False)[0]
        if self.device is None:
            actual = getattr(self.model, "device", None)
            if actual is not None:
                self.device = str(actual)
        names = r.names
        out = []
        for b in r.boxes:
            x1, y1, x2, y2 = b.xyxy[0].tolist()
            out.append(Detection(names[int(b.cls)], (x1, y1, x2, y2), float(b.conf)))
        return out


class YoloDetector:
    """CLOSED-vocab YOLO (COCO-80). Fixed classes, but ROBUST when you 'place it anywhere':
    it MISSES objects it doesn't know rather than force-fitting a wrong label onto them (which
    is what an open-vocab model does on a mismatched prompt). Graceful degradation is safer for
    the gate (a wrong+flickery box = a false structural event). No vocabulary needed.

        pip install ultralytics      # weights auto-download on first use
    """
    def __init__(self, weights: str = "yolov8s.pt", conf: float = 0.3, device: str = None):
        try:
            from ultralytics import YOLO
        except ImportError:
            raise SystemExit("Closed YOLO needs ultralytics:  pip install ultralytics")
        self.model = YOLO(weights)
        self.conf = conf
        self.device = device

    def detect(self, image) -> List[Detection]:
        r = self.model.predict(image, conf=self.conf, device=self.device, verbose=False)[0]
        names = r.names
        out = []
        for b in r.boxes:
            x1, y1, x2, y2 = b.xyxy[0].tolist()
            out.append(Detection(names[int(b.cls)], (x1, y1, x2, y2), float(b.conf)))
        return out


class GroundingDinoDetector:
    """Open-vocab Grounding DINO (HF transformers). More accurate / cleaner boxes than YOLO-World,
    but MUCH slower (transformer + text encoder) — fine for the scanning rig (a few frames per
    pose), likely too slow for a 30fps stream. Same .detect() interface.

        pip install torch transformers pillow
        det = GroundingDinoDetector(["person","chair","desk","robot arm"])
    """
    def __init__(self, vocabulary: List[str], model="IDEA-Research/grounding-dino-tiny",
                 conf: float = 0.3, device: str = None):
        try:
            import torch
            from transformers import AutoProcessor, AutoModelForZeroShotObjectDetection
        except ImportError as exc:
            raise SystemExit("Grounding DINO needs torch, transformers and pillow. Install "
                             "requirements-cv.txt first.") from exc
        if device is None:
            device = ("mps" if torch.backends.mps.is_available()
                      else "cuda" if torch.cuda.is_available() else "cpu")
        self.device = device
        self.conf = conf
        self.model_name = model
        self.proc = AutoProcessor.from_pretrained(model)
        self.model = AutoModelForZeroShotObjectDetection.from_pretrained(model).to(device)
        self.model.eval()
        self.vocab = _normalise_vocabulary(vocabulary)
        self._text_form = None      # "batch" | "string", decided on first use
        if not self.vocab:
            raise ValueError("Grounding DINO vocabulary must contain at least one label")
        print(f"[detector] Grounding DINO ({model}) on {device}")

    def set_vocab(self, vocabulary):          # dynamic open-vocab: planner 'detect' drives the prompt
        vocab = _normalise_vocabulary(vocabulary)
        if vocab:
            self.vocab = vocab

    def detect(self, image) -> List[Detection]:
        import torch, cv2
        from PIL import Image
        H, W = image.shape[:2]
        pil = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
        try:
            return self._detect(pil, (H, W), torch)
        except (NotImplementedError, RuntimeError) as exc:
            # Grounding DINO occasionally reaches an operator that a particular
            # PyTorch/MPS release has not implemented. A smoke test should expose
            # the fallback rather than make Apple Silicon look completely broken.
            if self.device != "mps":
                raise
            print(f"[detector] MPS inference failed ({exc}); retrying on CPU")
            self.device = "cpu"
            self.model = self.model.to("cpu")
            return self._detect(pil, (H, W), torch)

    def _encode(self, pil, torch):
        """Tokenise the vocabulary in whichever shape this Transformers wants.

        Compatibility shim, same species as _postprocess_grounding_dino below.
        Newer GroundingDinoProcessor takes a BATCH OF LABEL LISTS -- text=[[...]]
        -- which preserves multi-word phrases like "cell phone" all the way into
        the returned detections. Older ones take Grounding DINO's original wire
        format: one dot-separated string, "person. laptop. cell phone.".

        Handing the nested list to an older tokenizer raises

            TypeError: TextEncodeInput must be Union[TextInputSequence, ...]

        which says nothing about vocabularies, versions, or what to do. And
        requirements.txt does not pin `transformers`, so which branch you get
        depends on the day you ran pip. Try the structured form, fall back to the
        string, and remember which one worked so this costs one exception per
        process rather than one per frame.
        """
        if self._text_form != "string":
            try:
                out = self.proc(images=pil, text=[self.vocab],
                                return_tensors="pt").to(self.device)
                self._text_form = "batch"
                return out
            except (TypeError, ValueError) as exc:
                if self._text_form == "batch":
                    raise            # it worked before; this is a real failure
                print(f"[detector] this transformers wants the flat prompt "
                      f"({type(exc).__name__}); using 'a. b. c.' form")
                self._text_form = "string"
        prompt = ". ".join(self.vocab) + "."
        return self.proc(images=pil, text=prompt,
                         return_tensors="pt").to(self.device)

    def _detect(self, pil, image_hw, torch) -> List[Detection]:
        H, W = image_hw
        # Current Transformers accepts a batch of label lists. Keeping the
        # vocabulary structured preserves phrases such as "cell phone" and
        # "robot arm" all the way into the returned detections.
        inputs = self._encode(pil, torch)
        with torch.inference_mode():
            outputs = self.model(**inputs)
        result = _postprocess_grounding_dino(
            self.proc, outputs, inputs.input_ids, (H, W), self.conf)
        return _grounding_dino_detections(result, self.vocab)


def _normalise_vocabulary(vocabulary) -> List[str]:
    """Normalise planner labels without destroying multi-word object names."""
    seen, out = set(), []
    for value in vocabulary or []:
        label = " ".join(str(value).strip().lower().split())
        if label and label not in seen:
            out.append(label)
            seen.add(label)
    return out


def _postprocess_grounding_dino(processor, outputs, input_ids, image_hw, conf):
    """Compatibility shim for the Transformers threshold keyword rename."""
    import inspect
    fn = processor.post_process_grounded_object_detection
    threshold_kw = ("threshold" if "threshold" in inspect.signature(fn).parameters
                    else "box_threshold")
    return fn(outputs, input_ids, text_threshold=conf,
              target_sizes=[image_hw], **{threshold_kw: conf})[0]


def _as_python(value: Any):
    if hasattr(value, "item"):
        return value.item()
    return value


def _grounding_dino_detections(result, vocabulary) -> List[Detection]:
    """Convert HF post-processing output to the detector-neutral data model.

    Transformers releases have returned either ``text_labels`` (phrases) or
    ``labels`` (phrases/indices). Supporting both here keeps the model adapter
    small and makes this conversion unit-testable without loading checkpoints.
    """
    labels = result.get("text_labels")
    if labels is None:
        labels = result.get("labels", [])
    detections = []
    for box, score, raw_label in zip(result.get("boxes", []),
                                     result.get("scores", []), labels):
        values = box.tolist() if hasattr(box, "tolist") else list(box)
        x1, y1, x2, y2 = (float(v) for v in values)
        label_value = _as_python(raw_label)
        if isinstance(label_value, (int, float)) and int(label_value) == label_value:
            index = int(label_value)
            label = vocabulary[index] if 0 <= index < len(vocabulary) else str(index)
        else:
            label = " ".join(str(label_value or "object").strip().lower().split())
        detections.append(Detection(label or "object", (x1, y1, x2, y2),
                                    float(_as_python(score))))
    return detections


def make_detector(kind: str, vocabulary: List[str], conf: float = 0.3, device: str = None):
    """Factory so callers can swap detectors. Grounding DINO is the default."""
    kind = (kind or "gdino").lower()
    if kind in ("yoloworld", "world", "yw"):
        return YoloWorldDetector(vocabulary, conf=conf, device=device)
    if kind in ("yolo", "coco", "closed"):
        return YoloDetector(conf=conf, device=device)
    if kind in ("gdino", "groundingdino", "dino", "gd"):
        return GroundingDinoDetector(vocabulary, conf=conf, device=device)
    if kind in ("mock", "none"):
        return MockDetector([])
    raise SystemExit(f"unknown --detector '{kind}' (use: gdino | yoloworld | yolo | mock)")
