# VANSH

VANSH is a net coach that watches a cricket net through CCTV, at [vansh.best](https://vansh.best).

- **Every ball, measured.** Upload or record the cameras and VANSH finds each ball's flight, then measures pace, length and line. It also says whether the ball would have hit the stumps.
- **The action, read.** A pose model in the browser reads the bowling action or the batting stance and gives three coaching cues.
- **The next drill, bowled.** The coach marks how the batter played each ball. VANSH finds the zone where the batter was beaten most often and queues that drill for a bowling machine.

Every number is a measurement with an error, and each ball says how sure it is. A ball marked "check this mark" should be confirmed by the coach.

## How it fits together

```
 at the nets                                   on a server (vansh.best)
 ┌───────────────────────┐   recordings    ┌──────────────────────────────────┐
 │ side-on CCTV  ─┐      │ ──────────────▶ │ find flights → track → measure   │
 │ behind CCTV   ─┼─ edge bridge          │ sessions, pitch map, drills       │
 │ bowling machine◀┘      │ ◀────────────── │ machine queue                    │
 └───────────────────────┘   balls to bowl └──────────────────────────────────┘
```

The server never reaches into the club's network. The edge bridge, `python -m crickcoach.edge`, runs on a laptop or a small computer at the nets. It records both cameras at the same moment and uploads each take, and it collects balls from the queue for the machine. On a laptop at the nets, VANSH can also run whole, with no server.

## Run it

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m crickcoach            # http://127.0.0.1:8000
```

With no `VANSH_COACH_KEY` set, everything is open. That suits a laptop on the net's own network. Set the key before VANSH faces the internet.

| Setting | What it does |
|---|---|
| `VANSH_COACH_KEY` | Sign-in key for the coach. Sessions, video and the machine need it. The lane stays open. |
| `VANSH_DATA` | Where sessions, video and the lane calibration live. |
| `VANSH_MACHINE` | Unset for the simulator, `host:port` for a machine on the same network, or `edge` for a machine behind the edge bridge. |
| `VANSH_RADAR_PORT` | A localhost port where a radar gun sends one km/h reading per line. |
| `VANSH_ALLOW_RTSP` | Let the server record RTSP cameras itself. It is on by default without a coach key and off with one. |
| `VANSH_RTSP_PRIVATE` | Allow private camera addresses. It is on by default without a coach key and off with one. |
| `VANSH_MAX_GB` | Refuse uploads once sessions use this much disk. The default is 20. |

## Cameras

Two fixed cameras cover a net. Either one works alone.

- **The side-on camera measures pace and length.** Put it square to the middle of the pitch, 10 to 15 m from the stump line, with both sets of stumps in view. Note its distance from the stump line, because VANSH uses it to correct pace.
- **The camera behind the bowler measures the line.** Put it high, 3 to 5 m up and 6 to 10 m behind the bowler's stumps, looking down the pitch.

The shutter decides whether the ball can be seen. Set each camera to a fixed shutter of 1/500 s or faster. In the benchmark below, VANSH found the ball's length on almost every ball at 1/250 s, and on none at 1/60 s. Many CCTV cameras choose 1/50 s or slower in dim light unless the shutter is fixed.

Frame rate and ball colour help too:

- **Frame rate.** 50 frames a second beats 25. Pace error fell from about 5 km/h to about 3 km/h in the benchmark.
- **Ball colour.** A white or pink ball is easier to track than a red one. H.264 at CCTV bitrates discards much of a small red ball's colour, so a red ball can drop out for a few frames. VANSH bridges dropouts of up to 0.17 s.
- **Resolution.** Use 1080p or 720p. VANSH works on frames up to 1280 pixels wide.

## Calibrate, once per camera position

Pause on a frame where the pitch is clear, press **Calibrate this camera**, and click the points in order. A magnifier follows the pointer.

- **Side-on camera:** the foot of the stumps at the bowler's end, then the top of those stumps. Then the foot of the stumps at the batter's end, then the top of those stumps. Add the camera's distance from the stump line if you know it.
- **Behind the bowler:** the four places where the popping creases meet the return creases. Start at the bowler's end, off side first.

VANSH checks the clicks for order and spread. It keeps the calibration for the lane, so the next session starts with it. Calibrate again when a camera moves.

## A session

1. **Upload or record.** Upload the side and behind files, record from CCTV, or send takes from the edge bridge.
2. **Wait while VANSH works.** It looks for ball flights on each calibrated camera. It lines up the two cameras' clocks from the bounces, merges each ball's two readings, and shows progress as it goes.
3. **Review each ball.** You see the track drawn on the video, the pitch, pace and stumps, and a pitch map seen from behind the bowler.
4. **Mark the outcome.** The choices are left, defended, attacked, missed, edged or bowled. A click on the pitch map overrides the cameras' mark. **Use the cameras' mark** brings theirs back.
5. **Queue the next drill.** VANSH picks the zone where the batter was beaten most often, as a share of the marked balls there, and queues six balls at the batter's pace.

Without a calibration, VANSH cannot tell a ball from a bowler walking back. Short clips stay as one ball; for longer ones, calibrate and press **Find the balls again**, or press **Mark a ball here** while scrubbing.

Pitch names follow ball-tracking pitch maps, in metres from the batter's stumps:

| Length | Metres from the stumps |
|---|---|
| Yorker | under 2 |
| Full | 2 to 6 |
| Good | 6 to 8 |
| Short of a length | 8 to 10 |
| Short | over 10 |

Line is measured from middle stump. The stumps span ±11.4 cm, and the line names are off stump, middle, leg stump, outside off, down leg, and wide on either side. The **Right-hander** and **Left-hander** switch decides which side is off.

## How accurate it is

The test reels in `footage/` are rendered by `crickcoach/netsim.py`, and each comes with its true answer. The simulator includes:

- **The ball:** gravity, air drag, swing, seam movement and a bounce.
- **The players:** a bowler who runs in and walks back, and a batter who presses forward.
- **The camera:** a wide CCTV lens, shutter blur, sensor noise and H.264 compression.

`bench/run.py` scores a tracker on 72 random deliveries, with calibration clicks off by 1.5 px as a coach's would be. It uses the windows the app cuts itself. Medians:

| Camera, ball, frame rate, shutter | Correct length band | Length error | Pace error | Line error |
|---|---|---|---|---|
| Side, red, 25 fps, 1/250 s | 12 of 12 | 0.14 m | 4.9 km/h | — |
| Side, white, 25 fps, 1/250 s | 11 of 12 | 0.11 m | 5.5 km/h | — |
| Side, red, 50 fps, 1/500 s | 11 of 12 | 0.17 m | 2.9 km/h | — |
| Side, red, 25 fps, 1/60 s | 0 of 12 | — | — | — |
| Behind, red, 25 fps, 1/250 s | 7 of 12 | 0.75 m | — | 8.5 cm |
| Behind, white, 50 fps, 1/500 s | 10 of 12 | 0.44 m | — | 3.8 cm |

The tracker VANSH started with, kept in `bench/grok_v1`, named the right length on 0 to 2 of 12 balls in every row. It locked onto the batter's pads, and it could not see a red ball at all.

A few known limits:

- **Pace from the side camera alone** reads 2 to 3% low or high when the ball travels 0.3 m off the stump line. With both cameras, VANSH corrects this from the release line.
- **The behind camera measures no pace.** Seen from behind, a faster ball that drops faster looks the same.
- **Real footage.** Nothing here has yet been checked against real net footage with a radar gun. That is the next test.

```bash
python -m bench.run make --out /tmp/vansh-bench --n 12
python -m bench.run score --set /tmp/vansh-bench --tracker vansh --window app
python -m bench.run score --set /tmp/vansh-bench --tracker grok --window oracle
```

## The edge bridge at the nets

```bash
# record both cameras for two minutes and send the take
python -m crickcoach.edge record --server https://vansh.best \
    --side rtsp://192.168.1.20/stream --behind rtsp://192.168.1.21/stream --seconds 120

# keep sending takes until stopped
python -m crickcoach.edge record ... --loop

# bowl the coach's queue on the machine at the nets
python -m crickcoach.edge machine --server https://vansh.best --machine 192.168.1.50:8520 --armed
```

The coach key comes from `--key` or `VANSH_COACH_KEY`. Run the server with `VANSH_MACHINE=edge` so that **Bowl next** sends balls to the bridge.

A bowling machine throws a hard ball at a person, so the bridge has three safeguards:

- **Armed by hand.** It will not bowl unless started with `--armed`.
- **A countdown.** It counts down before every ball.
- **A press of Enter.** By default it waits for Enter at the nets before each ball. `--auto` removes the wait, never the countdown.

## Bowling machine protocol

A machine listens on TCP for one JSON line and replies with one:

```json
{"cmd": "bowl", "id": "ab12cd34", "pace_kph": 125, "line": "off stump", "length": "good", "length_m": 7.0, "offset_m": -0.1, "batter": "right"}
{"ok": true, "id": "ab12cd34"}
```

- **`length_m`** is metres from the batter's stumps.
- **`offset_m`** is metres from middle stump, positive towards a right-hander's leg side. For a left-hander it is already mirrored.

`serve` in `crickcoach/machine_link.py` is a reference listener for a bridge to copy. Every queued ball is also appended to `machine/spool.jsonl` in the data folder.

A radar reading can replace the cameras' pace: post `{"pace_kph": 138}` to `/api/radar`, or send one number per line to `VANSH_RADAR_PORT`. Then press **Use radar** on the ball.

## Coaching cues

The pose model, MediaPipe Pose Landmarker, runs in the browser. **Read the action** samples the ball's window 25 times a second.

- **Bowling,** side-on: is the arm straight at release, the head steady, the front knee braced, and the release high?
- **Batting,** front-on: the knee bend, the head over the base, the hands up, the backlift, and the width of the base.

Angles are measured in the picture, not in three dimensions, so they are checks for a coach to confirm. They are not a legality test for the bowling arm.

## Deploy to a server

`deploy/vps.sh` puts VANSH on a Linux server where Caddy, running in Docker, serves the sites.

```bash
DEPLOY_HOST=root@your-server ./deploy/vps.sh
```

Your server's settings, such as the domain and where the Caddyfile lives, can go in `deploy/vps.local.env`, which git ignores. The script is safe to run again. It does the following:

- **Code.** Puts the code in `/var/www/vansh`, owned by root, and the data in `/var/lib/vansh`.
- **Settings.** Writes `/etc/vansh/vansh.env` once, with a new coach key.
- **Service.** Installs a hardened systemd service on port 8094, behind Caddy's proxy headers.
- **Firewall.** Lets only the Docker bridge reach that port through ufw.
- **Caddy.** Adds a marked block for the domain to the Caddyfile. It backs up the Caddyfile, validates the change, and reloads. If Caddy rejects the change, it restores the backup.

To read the coach key:

```bash
ssh root@your-server cat /etc/vansh/vansh.env
```

## Tests

```bash
PYTHONPATH=. .venv/bin/python -m unittest discover -s tests
```

The 69 tests take about half a minute. They cover the physics, the measurement, the raw reels end to end, the API with and without a coach key, the machine in all three modes, and the edge bridge's safeguards. They pass on Python 3.12, the VPS's version, and on 3.15.

To render new reels:

```bash
PYTHONPATH=. python -c "from crickcoach.footage import export_footage; export_footage()"
```
