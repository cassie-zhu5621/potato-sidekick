"""planning — VLM: turning a spoken request into something the CV can evaluate.

  gemini_provider.py shared low-latency structured-output client
  planner.py     request + independent spatial images -> compiled watch-spec
  spec_utils.py  the relevance layer: tiering -> detector vocabulary -> focus gate
  sweep_plan.py  pure frames per station -> ONE multi-image Gemini call
  plan_view.py   holds the engine + executor, publishes THE PLAN panel
  event_frames.py selects five ordered temporal evidence frames
  judge.py       confirms candidates and narrates stories with Gemini
"""
