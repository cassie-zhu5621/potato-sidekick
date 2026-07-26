"""session — One session: the state machine and everything that talks to a participant.

  session_flow.py  the transition rules, with NO hardware in them (unit-tested)
  stt.py           push-to-talk recording + Whisper, and the manual door
  storyboard.py    a finding is a story: keyframed burst -> comic strip
  cores3_link.py   the CoreS3 I/O board: screen, sound, touch
  feed.py          the single writer of the NOTICED feed
"""
