# VANSH

VANSH is the net coach at [vansh.best](https://vansh.best). Two fixed cameras watch a session. Each ball is cut out, the body is read for three coaching cues, and the ball is tracked for pace, line, and length. Misses become the next drill, and that drill is sent to a bowling machine.

## Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m crickcoach
```

Open http://127.0.0.1:8000. The deployed coach is https://vansh.best.

A phone or tablet on the same network can use the screen if you start it with:

```bash
python -m crickcoach --host 0.0.0.0
```

Anyone who can reach that address can start a camera recording, so use it on the net's own network.

## What you do in the lane

- **Lane.** Start a camera, or load a sample action. The skeleton is drawn on the picture and three cues update while you bowl or hold a stance. Save the ball to the session log.
- **Sessions.** Upload the side-camera video. An optional second file is the camera behind the bowler. The clip is cut on motion, one window per delivery. **Read the action** measures the body. **Calibrate camera**, then **Track the ball**, measures the flight. Mark how the batter played it: left, defended, attacked, missed, edged, or hit.
- **Machine.** The queue is line, length, and pace. **Queue the next drill** looks at the misses and repeats the length that beat the batter. **Bowl next** sends a ball. With no machine address this is a simulator, and the ball moves to the bowled list.

The raw test reels live in `footage/` and are what the coach is checked against:

- `side_two_balls.mp4` is two deliveries with a quiet gap. **Load raw side clip** cuts it into two balls.
- `side_good_length.mp4` is one side-on ball. **Load raw tracked ball** reads a good length and a pace.
- `behind_off_good.mp4` is one ball from behind the bowler. **Load raw behind ball** reads good length on off stump.

`footage/manifest.json` holds the calibration and the result each reel must produce. The reels have no player in them, so **Read the action** reports that nobody is in frame.

## Ball tracking

Calibrate once per camera, on a frame where the pitch is clear.

Side camera, in order: bowling crease on the pitch, batting crease on the pitch, top of the off stump. This camera gives length and pace. It does not give the line. Pace uses the stumps as a 71 cm ruler and the 20.12 m between the stumps.

Behind the bowler, in order: off and leg corners at the bowler's end, then leg and off corners at the batter's end. The bounce gives line and length. Pace assumes a 2 m release, because that camera does not see height. A ball on middle stump at the batter's end is marked as hitting the stumps.

Both numbers are estimates from one camera. A coach still checks the mark. A radar reading replaces the camera pace: post `{"pace_kph": 138}` to `/api/radar`, or set `CRICKCOACH_RADAR_PORT` and send one number per line.

## Bowling machine

Each queued ball is also appended to `data/machine/spool.jsonl`. The bowled log is `data/machine/bowled.json`.

A machine listens for one JSON line and replies with one JSON line:

```json
{"cmd": "bowl", "id": "ab12cd34", "pace_kph": 125, "line": "off", "length": "good"}
{"ok": true, "id": "ab12cd34"}
```

```bash
VANSH_MACHINE=192.168.1.50:8520 python -m crickcoach
```

`VANSH_DATA` and `VANSH_RADAR_PORT` are the matching settings. The older `CRICKCOACH_*` names still work.

## vansh.best

Point the DNS for vansh.best at the net computer, then proxy HTTPS to the app:

```
vansh.best {
    reverse_proxy 127.0.0.1:8000
}
```

The coach already calls itself VANSH and uses https://vansh.best as its public address.

`serve` in `crickcoach/machine_link.py` is a reference listener a bridge can copy. Leave `VANSH_MACHINE` unset to bowl into the simulator.

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
