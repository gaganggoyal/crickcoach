# VANSH

VANSH is a net coach that watches a cricket net through CCTV, at [vansh.best](https://vansh.best).

- **Every ball, measured.** Upload or record the cameras and VANSH finds each ball's flight, then measures pace, length and line. It also says whether the ball would have hit the stumps.
- **The action, read.** A pose model in the browser reads the bowling action or the batting stance and gives three coaching cues.
- **The next drill, bowled.** The coach marks how the batter played each ball. VANSH finds the zone where the batter was beaten most often and queues that drill for a bowling machine.

Every number is a measurement with an error, and each ball says how sure it is. A ball marked "check this mark" should be confirmed by the coach.

**Setting up real cameras?** The [setup guide](docs/SETUP.md) covers what to buy, where each camera goes, the camera settings, the bridge at the nets, calibration and checking the numbers. It is also at [vansh.best/setup](https://vansh.best/setup/), with a placement calculator and a camera address builder.

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
| `VANSH_MAX_GB` | Most video kept for sessions, in GB. The default is 20. When a new upload needs room, the oldest videos are removed first; their measurements, outcomes and cues stay. |

## Cameras

The [setup guide](docs/SETUP.md) covers this in full, with diagrams. In short:

- **Side-on camera, for pace and length.** Put it 10 to 15 m from the line between the middle stumps, level with the middle of the pitch, 2 to 3 m up.
- **Camera behind the bowler, for the line and the stumps.** Put it 8 m behind the bowler's stumps, 6 m up, over the line of middle stump, with about 50° of view. From 4.5 m up, the bowler's follow-through hid short balls. Use a white or pink ball and 50 frames a second for this camera.
- **Shutter.** Fix every camera's shutter at 1/500 s. In the benchmark below, VANSH found the length on every ball at 1/250 s and on none at 1/60 s. Many CCTV cameras choose 1/50 s or slower in dim light unless the shutter is fixed.

## Calibrate, once per camera position

Pause on a frame where nobody stands in front of the stumps, press **Calibrate this camera**, and click four points. A magnifier follows the pointer.

- **Side-on camera:** the foot and then the top of the stumps at the bowler's end, then the same at the batter's end. Add the camera's distance from the stump line.
- **Behind the bowler:** the four places where the popping creases meet the return creases, bowler's end first, starting on the left of the picture.

VANSH checks the clicks for order and spread, and keeps the calibration for the lane. Calibrate again when a camera moves.

## A session

1. **Record or upload.** With the bridge running at the nets, press **Start recording**: a take arrives every 90 seconds, and the takes are grouped under the recording's name. Or upload the side and behind files.
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
| Behind (8 m back, 6 m up), white, 50 fps, 1/500 s | 11 of 12 | 0.12 m | — | 2.9 cm |
| Behind (8 m back, 6 m up), red, 25 fps, 1/250 s | 5 of 12 | 1.53 m | — | 11.7 cm |

The tracker VANSH started with, kept in `bench/grok_v1`, named the right length on 0 to 2 of 12 balls in every row. It locked onto the batter's pads, and it could not see a red ball at all.

The camera behind the bowler is placed as the guide says, with all four crease corners in the picture for the coach to click. An earlier version of this benchmark aimed it so the near corners were out of view, which no coach could calibrate. Mounting positions were compared on the same twelve deliveries; at 4.5 m up, the bowler hid some short balls until they pitched.

A few known limits:

- **Pace from the side camera alone** reads 2 to 3% low or high when the ball travels 0.3 m off the stump line. With both cameras, VANSH corrects this from the release line.
- **The behind camera measures no pace.** Seen from behind, a faster ball that drops faster looks the same.
- **A red ball from behind.** From 6 m up at 25 frames a second, a red ball is hard to see against the pitch. Use a white or pink ball and 50 frames a second for the line.
- **Real footage.** Nothing here has yet been checked against real net footage with a radar gun. That is the next test.

```bash
python -m bench.run make --out /tmp/vansh-bench --n 12
python -m bench.run score --set /tmp/vansh-bench --tracker vansh --window app
python -m bench.run score --set /tmp/vansh-bench --tracker grok --window oracle
```

## The bridge at the nets

The bridge runs on a laptop, mini PC or Raspberry Pi next to the cameras. It needs only `requirements-edge.txt`.

```bash
pip install -r requirements-edge.txt
python -m crickcoach.edge check     # read each camera for five seconds and say how usable it is
python -m crickcoach.edge run       # record whenever the coach presses Start, and send every take

# bowl the coach's queue on the machine at the nets
python -m crickcoach.edge machine --machine 192.168.1.50:8520 --armed
```

- **Settings.** They come from `vansh-edge.env`: the server, the coach key and each camera's RTSP address. See [the example](docs/vansh-edge.env.example).
- **Recording.** While the coach's recording runs, the bridge records every camera without a break. It keeps the cameras' own H.264 rather than re-encoding it, and cuts a take every 90 seconds.
- **Sending.** It pairs the two cameras' takes by time and sends each pair as it finishes. If the internet drops, takes wait on disk.
- **Reporting.** The coach screen shows the bridge, its cameras and the takes.
- **Installing.** [Section 5 of the guide](docs/SETUP.md#5-the-bridge-at-the-nets) covers installing it and starting it with the computer.

Run the server with `VANSH_MACHINE=edge` so that **Bowl next** sends balls to the bridge. A bowling machine throws a hard ball at a person, so the bridge has three safeguards:

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

The 83 tests take about a minute. They cover the physics, the measurement, the raw reels end to end, the API with and without a coach key, recording at the nets through the bridge, the machine in all three modes, and the bridge's safeguards. They pass on Python 3.12, the VPS's version, and on 3.15.

To render new reels:

```bash
PYTHONPATH=. python -c "from crickcoach.footage import export_footage; export_footage()"
```
