"""perception — CV: what is in the frame, and what the people in it are doing.

  perceive.py    detectors (COCO YOLO / YOLO-World / GroundingDINO) + scene graph
  gaze.py        MediaPipe face + pose, head-pose rays, drawing primitives
  relations.py   RelationEngine: one frame -> the 11-row truth vector + viz
  watch_exec.py  WatchExecutor: truth vector -> which watch entries fired
  overlay.py     everything drawn on a frame
"""
