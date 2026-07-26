"""planning — VLM: turning a spoken request into something the CV can evaluate.

  planner.py     request + image -> compiled watch-spec (the 11-row grammar)
  spec_utils.py  the relevance layer: tiering -> detector vocabulary -> focus gate
  sweep_plan.py  SWEEP-FIRST planning: pure frames per station -> ONE VLM call
  plan_view.py   holds the engine + executor, publishes THE PLAN panel
  judge.py       narrates a finished story from its grounded trace
"""
