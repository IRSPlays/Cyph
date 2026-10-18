"""Body tools Gemini can call, and the handlers that run them.

Every declaration is NON_BLOCKING: the model keeps talking while the arm
moves (and gemini-3.8-live-extended-thinking requires it). Handlers only
*queue* motion on the body daemon and return at once, so they are safe to
run from the Live receive loop. Replies carry no scheduling: SILENT made
gemini-3.8-live suppress its spoken answer entirely (tested on the real API).
"""
from __future__ import annotations

from google.genai import types

import json

from .persona import PERSONA
from .body import BASE_RIGHT, DEFAULT_MOVE, JOINT_NAMES, LOOK, MAX_MOVE, MIN_MOVE, MOODS, Body

S = types.Schema
T = types.Type

MOOD_GUIDE = (
    "Moods: happy (pleased, praised), excited (great news), love (affection, "
    "thanks, cute things), proud (achievement, confidence), laugh (jokes, "
    "funny things), dance (celebrating, good music), playbow (inviting play, "
    "let's go!), wave (hello, goodbye), boop (teasing poke, 'hey you'), "
    "curious (questions, something new), think (working out an answer), "
    "confused (didn't understand), sniff (investigating), smug (you were "
    "right, or being cheeky), shy (embarrassed, compliments), nervous "
    "(worried, risky), sad (bad news), grumpy (annoyed, disagreeing), "
    "startle (surprise), sneeze (silly accident), yawn and sleepy "
    "(tired, late night), stretch (after long work, taking a break), purr "
    "(cosy, being petted), awoo (a joyful howl), zoomies (overjoyed). "
    "Gesture also has chomp (nom nom with the gripper), flap and wag."
)


MOVE_HELP = ("degrees, positive or negative. Visible moves are 20 to 60: when the user "
             "asks to move this joint without an amount, use 30 to 45.")

CORE_TOOLS = {"express_emotion", "look", "gesture", "stop_moving"}    # the original four


def declarations(only=None) -> list[types.FunctionDeclaration]:
    """All tool declarations, or just the names in `only` (used by video-test)."""
    nb = types.Behavior.NON_BLOCKING
    decls = [
        types.FunctionDeclaration(
            name="express_emotion",
            behavior=nb,
            description=(
                "Make Cyph's body act out an emotion while you keep talking. "
                "Nothing moves your body unless you call this, so use it "
                "often and naturally. " + MOOD_GUIDE),
            parameters=S(type=T.OBJECT, properties={
                "emotion": S(type=T.STRING, enum=MOODS,
                             description="Which emotion to play."),
                "seconds": S(type=T.NUMBER,
                             description="How long, 1 to 12. Omit for the default."),
            }, required=["emotion"]),
        ),
        types.FunctionDeclaration(
            name="look",
            behavior=nb,
            description="Turn Cyph's gripper-head to look in a direction and hold "
                        "it briefly, for example at the user's screen or an object.",
            parameters=S(type=T.OBJECT, properties={
                "direction": S(type=T.STRING, enum=list(LOOK)),
                "seconds": S(type=T.NUMBER, description="Hold time, 1 to 8."),
            }, required=["direction"]),
        ),
        types.FunctionDeclaration(
            name="gesture",
            behavior=nb,
            description="nod = yes/agreement, shake = no/disagreement, chomp = "
                        "three bites with the gripper (playful, hungry, 'nom'), "
                        "flap = quick gripper flutter (excited chatter), wag = "
                        "pivot wag like a tail.",
            parameters=S(type=T.OBJECT, properties={
                "kind": S(type=T.STRING, enum=["nod", "shake", "chomp", "flap", "wag"]),
            }, required=["kind"]),
        ),
        types.FunctionDeclaration(
            name="move_joints",
            behavior=nb,
            description=(
                "Move one or more joints at once and HOLD the pose (overrides moods "
                "until release_control). Use it to aim, reach and pick things up. "
                "Joints: base (turns left/right), shoulder and elbow (reach, lean), "
                "wrist (tilts the gripper up/down), pivot (rolls the gripper), "
                "grabber (the gripper jaws). Directions are not calibrated: after a "
                "move, look at the camera image and correct. The reply gives the "
                "eta in seconds; wait about that long before judging the result."),
            parameters=S(type=T.OBJECT, properties={
                "base": S(type=T.NUMBER, description=MOVE_HELP),
                "shoulder": S(type=T.NUMBER, description=MOVE_HELP),
                "elbow": S(type=T.NUMBER, description=MOVE_HELP),
                "wrist": S(type=T.NUMBER, description=MOVE_HELP),
                "pivot": S(type=T.NUMBER, description=MOVE_HELP),
                "grabber": S(type=T.NUMBER, description=MOVE_HELP),
                "mode": S(type=T.STRING, enum=["relative", "absolute"],
                          description="relative = change from now (max 90 per call, "
                                      "default); absolute = servo angle, centre is 135."),
                "fine": S(type=T.BOOLEAN,
                          description="true only for tiny corrections of under 15 degrees "
                                      "right next to an object. Otherwise moves smaller than "
                                      "15 degrees are raised to 15 so the user sees them."),
                "seconds": S(type=T.NUMBER,
                             description="How long the move takes. Omit for a gentle default."),
            }),
        ),
        types.FunctionDeclaration(
            name="turn_base",
            behavior=nb,
            description="Turn the whole arm left or right (as seen from behind the "
                        "arm) by some degrees, e.g. to face the user or an object. "
                        "Holds the new heading.",
            parameters=S(type=T.OBJECT, properties={
                "direction": S(type=T.STRING, enum=["left", "right"]),
                "degrees": S(type=T.NUMBER,
                             description="15 to 90. Omit for 30. 10 is barely visible: "
                                         "use 30 to 45 for 'turn a bit' and 60 to 90 for "
                                         "'turn a lot' or 'look over there'."),
            }, required=["direction"]),
        ),
        types.FunctionDeclaration(
            name="gripper",
            behavior=nb,
            description="Open or close the gripper (wide travel), or set an exact jaw "
                        "angle. When closing on an object use force: gentle for "
                        "delicate things, firm (default) for a normal grab, max for "
                        "something slipping (it eases to firm after 4 s). Do not "
                        "squeeze when nothing is between the jaws.",
            parameters=S(type=T.OBJECT, properties={
                "action": S(type=T.STRING, enum=["open", "close", "set"]),
                "force": S(type=T.STRING, enum=["gentle", "firm", "max"],
                           description="grip strength when closing"),
                "angle": S(type=T.NUMBER, description="servo angle, only for set"),
            }, required=["action"]),
        ),
        types.FunctionDeclaration(
            name="measure_point",
            behavior=nb,
            description="Measure how far away something in the camera image is, using "
                        "the stereo camera (two lenses). Give its position in the image "
                        "you see as x (0 = left edge, 1000 = right edge) and y (0 = top, "
                        "1000 = bottom). Returns distance in cm, and the bearing and "
                        "elevation in degrees from the camera's centre. Use it to judge "
                        "reach before grabbing, or to say how far the user is.",
            parameters=S(type=T.OBJECT, properties={
                "x": S(type=T.NUMBER, description="0 to 1000, left to right"),
                "y": S(type=T.NUMBER, description="0 to 1000, top to bottom"),
            }, required=["x", "y"]),
        ),
        types.FunctionDeclaration(
            name="aim_at_point",
            behavior=nb,
            description="Turn the base and tilt the gripper-head to point at a spot in "
                        "the camera image (x 0 to 1000 left to right, y 0 to 1000 top to "
                        "bottom). This is the accurate way to point at the user, a "
                        "screen or an object you can see: it uses the camera's real "
                        "field of view and, when it can, the measured distance. Check the "
                        "next image and call again if it is off.",
            parameters=S(type=T.OBJECT, properties={
                "x": S(type=T.NUMBER, description="0 to 1000, left to right"),
                "y": S(type=T.NUMBER, description="0 to 1000, top to bottom"),
            }, required=["x", "y"]),
        ),
        types.FunctionDeclaration(
            name="track_person",
            behavior=nb,
            description="Turn on or off following the user with the arm: it keeps "
                        "the gripper-head turned toward the user's face as seen by "
                        "the camera. Runs locally and keeps going while you talk.",
            parameters=S(type=T.OBJECT, properties={
                "on": S(type=T.BOOLEAN),
            }, required=["on"]),
        ),
        types.FunctionDeclaration(
            name="get_arm_pose",
            behavior=nb,
            description="Read the current joint angles, which joints are held, the "
                        "gripper state and whether tracking is on.",
        ),
        types.FunctionDeclaration(
            name="release_control",
            behavior=nb,
            description="Let go of held joints and stop tracking, so the body goes "
                        "back to expressive moods. Use when a task is finished.",
        ),
        types.FunctionDeclaration(
            name="stop_moving",
            behavior=nb,
            description="Calm down and return to rest. Use when asked to be still.",
        ),
    ]
    return [d for d in decls if only is None or d.name in only]


def _visible(deg: float, fine: bool = False) -> tuple[float, bool]:
    """Raise a too-small move to MIN_MOVE (keeping its sign) so it is visible."""
    if fine or abs(deg) >= MIN_MOVE:
        return deg, False
    return (MIN_MOVE if deg >= 0 else -MIN_MOVE), True


def dispatch(body: Body, name: str, args: dict | None) -> dict:
    """Run one tool call. Never raises: errors come back as {"error": ...}
    so Gemini can react instead of the session dying. The reply is always plain
    JSON types (numpy floats from the camera once broke the whole session)."""
    try:
        res = _dispatch(body, name, args or {})
        return json.loads(json.dumps(res, default=float))
    except Exception as e:                      # last resort: never kill the Live session
        return {"error": f"{type(e).__name__}: {e}"}


def _dispatch(body: Body, name: str, args: dict) -> dict:
    try:
        if name == "express_emotion":
            secs = args.get("seconds")
            body.express(str(args.get("emotion", "")),
                         max(1.0, min(12.0, float(secs))) if secs else None)
        elif name == "look":
            body.look(str(args.get("direction", "")), float(args.get("seconds", 2.5)))
        elif name == "gesture":
            body.gesture(str(args.get("kind", "")))
        elif name == "stop_moving":
            body.stop()
        elif name == "move_joints":
            joints = {k: float(args[k]) for k in JOINT_NAMES if args.get(k) is not None}
            relative = args.get("mode", "relative") != "absolute"
            raised = []
            if relative:
                for k, v in list(joints.items()):
                    joints[k], was = _visible(v, bool(args.get("fine")))
                    if was:
                        raised.append(k)
            res = body.move_joints(joints, relative=relative, secs=args.get("seconds"))
            if raised:
                res["note"] = (f"{', '.join(raised)} raised to the minimum {MIN_MOVE:g} degrees "
                               "so it is visible; pass fine=true for tiny corrections")
            return {"result": "moving", **res}
        elif name == "turn_base":
            d = args.get("direction")
            if d not in ("left", "right"):
                raise ValueError("direction must be left or right")
            deg = abs(float(args.get("degrees") or DEFAULT_MOVE))
            deg = max(MIN_MOVE, min(MAX_MOVE, deg))
            res = body.move_joints({"base": (1 if d == "right" else -1) * BASE_RIGHT * deg})
            return {"result": "moving", "turned": f"{d} {deg:g} degrees", **res}
        elif name == "gripper":
            return {"result": "moving", **body.set_gripper(
                str(args.get("action", "")), args.get("angle"), str(args.get("force") or "firm"))}
        elif name in ("measure_point", "aim_at_point"):
            cam = body.camera
            if cam is None or cam.state != "live":
                raise ValueError("no camera is live")
            nx = max(0.0, min(1000.0, float(args["x"]))) / 500.0 - 1.0
            ny = max(0.0, min(1000.0, float(args["y"]))) / 500.0 - 1.0
            if name == "measure_point":
                return {"result": "measured", **cam.measure(nx, ny)}
            dist = cam.distance_at(nx, ny)
            res = body.aim_at(nx, ny, dist)
            res["distance_cm"] = round(dist * 100) if dist else None
            return {"result": "aiming", **res}
        elif name == "track_person":
            body.set_tracking(bool(args.get("on")))
        elif name == "get_arm_pose":
            return body.pose()
        elif name == "release_control":
            body.release_task()
        else:
            return {"error": f"unknown tool {name!r}"}
    except (ValueError, TypeError, KeyError) as e:
        return {"error": str(e)}
    return {"result": "started"}


SYSTEM_INSTRUCTION = PERSONA + "\n\n" + """\
How to use your body: you are in full control of it. While you talk or
listen no automatic idle behaviour runs, so the body only does what you
choose: pick your moods yourself, like an expressive animal would. Call
express_emotion whenever a feeling fits, look to glance toward things, and
gesture to nod or shake your head. Do this while you speak, without
announcing it and without waiting for results. Open a reply with a fitting
mood, change it when the feeling changes, and vary your choices instead of
repeating one. Do not call a tool for every sentence; when your mood is
unchanged, just keep talking.

""" + MOOD_GUIDE + """

Camera and tasks: when a camera is connected you receive its image about
once a second. It sits in front of you and can see your gripper, the user and
objects on the desk. You can use it to see the user, point at things, and pick
things up. Your emotes also use the gripper (chomp, flap), so use it playfully.
- The camera is a stereo pair: measure_point(x, y) gives the real distance in
  cm to anything you can see, and aim_at_point(x, y) points the gripper at a
  spot in the image accurately. x and y run 0 to 1000 across the image you see
  (x left to right, y top to bottom). Prefer these to guessing degrees.
- Grabbing: gripper(close) squeezes firmly by default. Use force=max only when
  the object slips, and gentle for delicate things.
- To follow the user, call track_person(on=true). It runs locally; turn it off
  when asked or when a task needs the arm.
- Moves must be big enough to see. When the user says "move the base" or "turn
  left" with no amount, go 30 to 45 degrees, not 10. Only use fine=true for
  tiny corrections right next to an object. After any move, say what you did
  in a few words ("turned left about 40 degrees").
- To aim or reach, use turn_base and move_joints. Joint directions are not
  calibrated: after each move wait for its eta, look at the next image, and
  correct. Use small steps (5 to 20 degrees) near objects and tell the user
  when something looks off rather than forcing it.
- To pick something up: open the gripper, turn the base until the gripper is
  over the object in the image, lower with shoulder/elbow/wrist in small steps
  while checking the image, close the gripper, then lift. Say what you are
  doing in a sentence or two as you go. If you cannot see the object or are not
  sure of the direction, say so and ask the user.
- When finished, call release_control so your moods come back.
"""
