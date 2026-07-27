"""robot — the servo bus, the calibration for THIS build, and the clip player.

Nothing in here knows about cameras, VLMs or the web UI. It takes a state name
and makes the robot perform it.

Four files, four levels of abstraction, and the dependencies run ONE WAY:

    states.py       the design, as a readable table: the eight states, their
                    clips, colours, sounds, screens and timeouts. Imports
                    nothing at all.
    calibration.py  the measured numbers for this physical build -- centre,
                    limits, offset, invert per joint.
    pose.py         the only place that converts between frames. Blender degrees
                    <-> clip units <-> commanded units, plus how long a move
                    should take. Depends on calibration.py.
    scs.py          the only place that speaks SCSCL: register map, packet
                    packing, port discovery. Depends on the vendor SDK and
                    nothing of ours; it knows servo IDs, not joint names.

    clip_player.py  ties the three together: reads a clip, resolves it through
                    pose.py, streams it over scs.py on the clip's own clock.

    tools/          standalone diagnostics. Nothing imports them.

So: changing the state table cannot touch the protocol, and changing the
protocol cannot touch the design. The one file to be careful with is pose.py --
it holds two coordinate frames (in Blender positive nod is UP; on the bus a
higher unit is DOWN), and getting that backwards makes the robot perform every
gesture inside out.
"""
