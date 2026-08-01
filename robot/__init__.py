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

# The joint -> servo ID map, defined once.
#
# This lived independently in pose.py, tools/jog.py, tools/play_on_hardware.py
# and tools/breath_test.py. Four copies of a fact that changes whenever a servo
# is swapped between joints -- and a partial edit produces the worst failure
# mode this robot has: jog.py agreeing that the "tilt" key moves the neck while
# the player drives that same ID as pan, so clips play with two channels
# transposed and nothing errors.
#
# IDs live in each servo's EEPROM, not in the wiring. Both GVS sockets on the
# FE-URT2 are the same electrical bus, so re-routing the loom cannot change
# them. Only physically swapping a servo between joints, or running
# check_bus.py --set-id, changes what belongs here.
IDS = {"pan": 1, "tilt": 2, "nod": 3}
ORDER = ["pan", "tilt", "nod"]
