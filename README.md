# CrickCoach

CrickCoach is a net-side cricket coach. Two fixed cameras watch a session, the clip is cut into one window per ball, and the coach screen gives three cues from the player's body. A bowling-machine queue stores the next ball to bowl.

This version is the lane you can run on a computer at the net. It records and reviews. It does not drive a bowling machine, and it does not measure ball speed from the picture.

## Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m crickcoach
```

Open http://127.0.0.1:8000

A phone or tablet on the same network can use the screen if you start it with:

```bash
python -m crickcoach --host 0.0.0.0
```

Anyone who can reach that address can start a camera recording, so use it on the net's own network.

## What you do in the lane

- **Lane.** Start a camera, or load a sample action. The skeleton is drawn on the picture and three cues update while you bowl or hold a stance. Save the ball to the session log.
- **Sessions.** Upload the side-camera video. An optional second file is the camera behind the bowler, kept for replay. The clip is cut on motion, one window per delivery. Open a ball, press **Read the action**, and click the pitch where it bounced. Pace is typed in, from a radar gun or a watch.
- **Machine.** Queue line, length, and pace. The current queue is `data/machine/queue.json`. Each new ball is also appended to `data/machine/spool.jsonl` for a later machine bridge.

Load a sample clip on the Sessions page to see the cutter without a real video. The sample has two bursts of motion and no player in it, so **Read the action** reports that nobody is in frame.

## Cameras

Pose cues want a **side-on** camera for bowling, and a **front or open-side** camera for batting. Frame the whole body. Pick **Left**, **Right**, or **Largest** when two people are in the picture.

The behind-the-bowler camera is for the coach's eye and for the pitch mark. A fast shutter matters once you care about the ball. A 1080p camera at 50 frames a second, high behind the bowler, is the useful setup. A slow dome camera still shows the players. It will not show the seam.

CCTV addresses must be `rtsp://` or `rtsps://`. Recordings are capped at 90 seconds per take. Longer sessions can be uploaded as a file.

## What the cues are

Angles are measured in the picture, so they describe a side-on bowling action and a front-on stance. They are coaching checks, and a coach still confirms them:

- Bowling: arm straight at release, head steady, front knee braced, release height.
- Batting: knee bend, head over the base, hands up. A moving backlift is read when the hands travel.

## Tests

```bash
PYTHONPATH=. .venv/bin/python -m unittest discover -s tests
```

## What comes next

Ball speed, line, and length from a fast camera, then a bridge that sends the queue to a programmable bowling machine. The queue file is the contract for that bridge.
