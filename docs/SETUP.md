# Set up VANSH at your nets

This guide takes a cricket net from no cameras to a coach screen that measures every ball. It covers what to buy, where each camera goes, how to set it, how to connect it, and how to check the numbers. The same guide, with a placement calculator and a camera address builder, is at [vansh.best/setup](https://vansh.best/setup/).

**The short version:**

- **Side-on camera (pace and length).** Put it 10 to 15 m from the line between the middle stumps, level with the middle of the pitch, 2 to 3 m up.
- **Camera behind the bowler (line, and whether the ball would hit the stumps).** Put it 8 m behind the bowler's stumps and 6 m up, over the line of middle stump.
- **Shutter.** Fix every camera's shutter at 1/500 s. A slow shutter turns the ball into a faint smear that no software can find.
- **Bridge computer.** A computer at the nets runs the VANSH bridge, which records both cameras and sends the video to vansh.best. The coach starts and stops recording from a phone.

## 1. What you need

### Three ways to start

| Setup | What it measures | What it needs |
|---|---|---|
| A phone on a tripod | Pace and length, after the session | A phone that films 1080p at 60 frames a second, and a tripod |
| One CCTV camera, side-on | Pace and length on every ball, while you bowl | One camera, a PoE switch, a computer at the nets |
| Two CCTV cameras | Also the line, and whether the ball would hit the stumps | Two cameras, a PoE switch, a computer at the nets |

**Start with a phone.** It's free and tests your net today. Put the phone side-on, where the side camera would go. Film at 1080p and 60 frames a second, in bright light or with the shutter set by hand. Then upload the video under **Sessions → Upload a net session**. iPhone video in H.265 is converted automatically. Move to fixed cameras when you want every ball measured without anyone holding a phone.

### What to look for in a camera

Check these on the datasheet before you buy.

- **Shutter you can fix.** You must be able to set the shutter (the "exposure time") to 1/500 s. This is the one feature you can't do without. Cameras sold for reading number plates always allow it; many cheap Wi-Fi cameras don't.
- **Frame rate.** 25 frames a second or more at 1080p. 50 or 60 is better, and the camera behind the bowler needs it.
- **Varifocal lens.** A motorised zoom lens, for example 2.7 to 13.5 mm, lets you frame the pitch from the ground. A fixed lens works if you choose the focal length with the calculator below.
- **Video format and network.** H.264 and RTSP, power over the network cable (PoE), and an IP66 rating for outdoor nets.
- **Picture settings that switch off.** WDR, backlight compensation (BLC) and noise reduction must be adjustable.

**Avoid:**

- **No exposure control.** Most home Wi-Fi cameras can't fix the shutter, so they pick a slow one whenever the light drops.
- **Low-light "colour at night" cameras** unless you have checked that the shutter can be fixed. Some leave it open as long as 1/12 s for night colour.
- **Fisheye and 180° cameras.**
- **Wi-Fi.** Every camera needs a cable.

### Everything else

- **PoE switch.** One port per camera. A camera without heaters draws under 10 W, so a standard 802.3af port is enough.
- **Cable.** Outdoor-rated Cat6, up to 100 m per camera.
- **Mounts.** A pole or bracket for the side camera. For the camera behind the bowler, either an arm that reaches out over the run-up or a fixing on the roof structure indoors.
- **Ball guards.** A steel mesh guard for any camera a ball can reach.
- **A computer at the nets for the bridge.** Any laptop from the last ten years, a mini PC or a Raspberry Pi 5 will do. The bridge copies the cameras' video without re-encoding it, so the computer doesn't need to be fast.
- **Internet upload speed** about equal to the cameras' bitrates added together. That's 8 to 12 Mbit/s for two cameras at 1080p. A slower line still works, because takes wait on the bridge computer and upload when they can.

## 2. Where the cameras go

![The side-on camera stands 12 m from the stump line, level with the middle of the pitch. The camera behind the bowler is 8 m behind the bowler's stumps.](img/top-view.svg)

The pitch is 20.12 m from stumps to stumps. Both cameras are placed relative to the line between the two middle stumps, called the stump line below.

### The side-on camera: pace and length

- **Distance.** 10 to 15 m from the stump line, measured at right angles to it. Write down the exact distance: VANSH asks for it when you calibrate and uses it to correct the pace.
- **Along the pitch.** Level with the middle of the pitch, so both sets of stumps are the same distance from the camera.
- **Height.** 2 to 3 m.
- **Aim.** Square across the pitch. Both sets of stumps should sit the same distance from the edges of the picture, with about 1.5 m of ground beyond each. That keeps the bowler's release and the batter in view.
- **Zoom.** As tight as that allows. Spare space on either side makes the ball smaller.

The field of view your lens gives decides how far back the camera goes. At these distances, the stumps plus 1.5 m beyond each fill the picture exactly:

| Horizontal field of view | Typical lens on a 1/2.8" sensor | Distance from the stump line |
|---|---|---|
| 100° | 2.8 mm | 9.7 m |
| 84° | 4 mm | 12.8 m |
| 70° | about 5 mm | 16.5 m |
| 54° | 6 mm | 22.7 m |
| 40° | 8 mm | 31.8 m |

At those distances the ball is about 4 pixels across in the picture VANSH works on, whatever the lens. Moving further back makes it smaller. Lenses wider than about 95° bend straight lines near the edges of the picture, and VANSH doesn't correct for that yet. So 70 to 90° from 12 to 16 m is the best choice.

**In a row of nets** the side camera usually looks across other lanes:

- **Netting.** Netting far from the lens is almost invisible, but netting a metre or two in front of it is not. Touch the lens to the net, or cut a small window in the net and cover it with a mesh guard.
- **Other lanes.** Players moving in the lanes in between are the hardest case. If you can, put the side camera on the open side of your net, or raise it to 3 or 4 m so your pitch sits above their heads in the picture.

### The camera behind the bowler: line and stumps

![Seen from the side, the camera 6 m up and 8 m behind the bowler's stumps sees from the near popping crease to the far stumps, and its view of a short ball passes over the bowler's head.](img/behind-side-view.svg)

- **Where.** On the line of middle stump, within about half a metre, 8 m behind the bowler's stumps.
- **Height.** 6 m. This matters more than it looks. From 4.5 m, the bowler's head and follow-through hid short balls until they pitched, and VANSH then misread their length by several metres. From 6 m, the view passes over the bowler.
- **Aim.** Down the pitch, tilted about 20° down, so the near popping crease sits just above the bottom of the picture and the far stumps near the top. All four corners where the popping creases meet the return creases must be in the picture, because you click them to calibrate.
- **Zoom.** About 50° of horizontal view, which is about 6 mm on a 1/2.8" sensor. A wider view makes the ball tiny at the batter's end. A narrower one can't hold both creases.
- **The run-up.** The bowler runs in underneath. Outdoors, fix the camera to an arm on a pole beside the run-up, never on a pole in it. Indoors, the roof structure is ideal.
- **Ball and frame rate.** Use a white or pink ball and 50 frames a second. From this camera a red ball at 25 frames a second is hard to see against the pitch (see [section 8](#8-check-the-numbers)).

### Work it out for your net

<div class="tool" data-tool="planner" markdown="0">
<p><em>The placement calculator runs on <a href="https://vansh.best/setup/">vansh.best/setup</a>. Enter the lens, distance, height, frame rate and shutter. It checks that both sets of stumps fit, how many pixels the ball covers and how much it blurs. For the camera behind the bowler, it also checks whether the view clears the bowler's head.</em></p>
</div>

### Light

- **Floodlights can flicker.** Many LED and sodium lamps flicker 100 times a second on 50 Hz mains. With a fast shutter the picture then pulses or shows dark bands, and VANSH sees movement everywhere. Use lights sold as flicker-free, or with high-frequency drivers. The camera check in section 5 shows the problem if a recording pulses.
- **Glare.** Don't point a camera at a floodlight or the low sun.
- **Infrared.** Turn infrared off and keep the camera in colour (day) mode. Infrared turns the ball grey and lights up the netting.

### Protect the cameras

A hard ball breaks a dome cover. Keep each camera behind netting or in a steel mesh guard. The camera behind the bowler is in the line of a straight drive, so it needs a guard.

## 3. Camera settings

Open each camera's web page by typing its IP address into a browser, then make these settings. Menu names vary between brands and firmware; the notes after the table say where to look.

| Setting | Set it to | Why |
|---|---|---|
| Shutter (exposure time) | **1/500 s**, or 1/1000 s in bright sun | At 140 km/h the ball moves 8 cm in 1/500 s and 78 cm in 1/50 s. VANSH found the length on 12 of 12 balls at 1/250 s and on none at 1/60 s. |
| Exposure mode | Manual, or shutter priority | So the camera can't slow the shutter down when the light drops |
| Anti-flicker | Outdoor, or off | A 50 Hz setting forces a slow 1/100 s shutter |
| Frame rate | 25, or 50 to 60 if offered | More pictures of each ball; pace is timed more closely |
| Resolution | 1920 × 1080, main stream | The sub-stream is too small |
| Video encoding | H.264 | Every browser plays it. H.265 works, but the server has to convert it first. |
| H.264+, H.265+, smart codec | Off | They space out keyframes and smear motion |
| Bitrate | Constant (CBR), 4 to 6 Mbit/s (4096 to 6144 kbps) | Heavy compression wipes out a small, fast ball. In tests, a low-bitrate copy lost the ball that the normal copy kept. |
| I-frame interval | The same as the frame rate, for example 25 | A keyframe every second, so each take starts cleanly |
| WDR, BLC, HLC | Off | They change the brightness from picture to picture |
| Noise reduction (DNR, 3D DNR) | Low, or off | It blends pictures over time, which smears a moving ball |
| Day/night | Day (colour), infrared off | See "Light" above |

**Where to find the settings, by brand:**

- **Hikvision, Prama Hikvision, HiLook.** The image settings are under Configuration → Image → Display Settings: exposure time, backlight (WDR and BLC), image enhancement (noise reduction), and the day/night switch. The stream settings are under Configuration → Video/Audio → Video: encoding, H.264+, bitrate type, maximum bitrate, frame rate and I-frame interval.
- **Dahua, and the many CP Plus and other cameras built on Dahua firmware.** The exposure mode, shutter and anti-flicker are under Setup → Camera → Conditions; newer firmware calls it Camera → Image. The main stream's compression, smart codec, frame rate, bitrate and I-frame interval are under Setup → Camera → Video.
- **Uniview.** Exposure is under Setup → Image → Image → Exposure. The shutter can be set in Manual or Shutter Priority mode. The stream settings are under Setup → Video & Audio.
- **Others.** Look for Exposure under Image or Camera. If you only see a "maximum shutter" or "exposure time" limit, set that to 1/500 s.

**The shutter can't be checked from the stream**, so check it by eye. Pause a recording on a ball in flight: a short, sharp streak is right, while a long faint smear means the shutter is still slow.

## 4. Network, power and camera addresses

- **Cabling.** Cable each camera to the PoE switch, and connect the switch to the club's router. The bridge computer joins the same network, by cable if possible.
- **Fixed addresses.** Give each camera a fixed IP address, either in the camera itself or as a reservation in the router, so the bridge always finds it.
- **A user for VANSH.** Make a user on each camera that can only view.
- **RTSP and the main stream.** Turn RTSP on (some cameras ship with it off; Tapo cameras need a "camera account" set in the app), and use the main stream.

Every camera has an RTSP address that the bridge reads. These are the usual patterns. Replace `user`, `password` and the IP address with your own.

| Brand | Main stream address |
|---|---|
| Hikvision, Prama, HiLook | `rtsp://user:password@192.168.1.20:554/Streaming/Channels/101` |
| Dahua, most CP Plus, Amcrest, Lorex | `rtsp://user:password@192.168.1.20:554/cam/realmonitor?channel=1&subtype=0` |
| CP Plus models on port 5543 (E-series) | `rtsp://user:password@192.168.1.20:5543/live/channel1` |
| Uniview | `rtsp://user:password@192.168.1.20:554/unicast/c1/s0/live` |
| Reolink | `rtsp://user:password@192.168.1.20:554/Preview_01_main` |
| TP-Link Tapo and VIGI | `rtsp://user:password@192.168.1.20:554/stream1` |
| Axis | `rtsp://user:password@192.168.1.20:554/axis-media/media.amp` |

There is no single address for CP Plus, so check your model's manual if both CP Plus patterns fail. Through an NVR, the channel number is the NVR input the camera is plugged into, for example `.../Channels/301` for input 3 on a Hikvision NVR. Reading each camera directly is better, because some NVRs re-encode the stream or cut the frame rate.

A password with symbols such as `@ # : /` must be written in a special form inside the address. The builder below does that for you.

<div class="tool" data-tool="rtsp" markdown="0">
<p><em>The camera address builder runs on <a href="https://vansh.best/setup/">vansh.best/setup</a>. It writes the line for the bridge's settings file in your browser and sends nothing anywhere.</em></p>
</div>

To test an address by hand, open it in VLC (Media → Open Network Stream). The bridge's check in the next section is more thorough.

## 5. The bridge at the nets

The bridge is a small program on a computer at the nets. It does four things:

- **Waits.** It does nothing until the coach presses **Start recording** on vansh.best.
- **Records.** It records both cameras without a break, in 90-second takes. It keeps the cameras' own video rather than re-encoding it, so even a small computer keeps up.
- **Sends.** Each take goes to vansh.best as soon as it is finished. If the internet drops, takes wait on the computer's disk, up to 20 GB, and go when it is back.
- **Reports.** It shows the coach screen which cameras it can see, their picture size and frame rate, and how many takes are waiting.

### Install it

You need Python 3.10 or newer from [python.org](https://www.python.org/downloads/). Linux and macOS usually have it already. ffmpeg is installed along with the bridge.

On Linux, a Raspberry Pi or macOS:

```bash
git clone https://github.com/gaganggoyal/crickcoach.git vansh
cd vansh
python3 -m venv .venv
.venv/bin/pip install -r requirements-edge.txt
```

On Windows, in PowerShell:

```powershell
git clone https://github.com/gaganggoyal/crickcoach.git vansh
cd vansh
py -m venv .venv
.venv\Scripts\pip install -r requirements-edge.txt
```

No git? On GitHub choose **Code → Download ZIP**, unzip it, and run the same commands from inside the folder.

### Tell it about your cameras

Copy `docs/vansh-edge.env.example` to `vansh-edge.env` in the `vansh` folder and fill it in:

```ini
VANSH_SERVER=https://vansh.best
VANSH_COACH_KEY=the-coach-key
VANSH_SIDE_URL=rtsp://vansh:password@192.168.1.20:554/Streaming/Channels/101
VANSH_BEHIND_URL=rtsp://vansh:password@192.168.1.21:554/Streaming/Channels/101
VANSH_TAKE_SECONDS=90
```

This file holds the coach key and the camera passwords, so keep it private. Leave out `VANSH_BEHIND_URL` if you have only the side camera.

### Check the cameras

```bash
.venv/bin/python -m crickcoach.edge check
```

On Windows, use `.venv\Scripts\python` in place of `.venv/bin/python`. The check reads each camera for five seconds and reports what it found. A good camera looks like this:

```text
Reading the side camera (rtsp://***@192.168.1.20:554/Streaming/Channels/101) for 5 s…
  ✓ 1920×1080 H264, 25 frames a second, 4.1 Mbit/s, a keyframe every 1 s.
  • 25 frames a second works. 50 or 60, if the camera offers it, measures pace more closely.
  Picture saved to vansh-check/side.jpg
Upload needed to keep up while recording: about 8 Mbit/s.
```

Open the saved pictures in `vansh-check/` and check that both sets of stumps (side) or both popping creases (behind) are in view. The result also appears on the coach screen. If a camera fails, the message says why: wrong address, wrong password, or no stream at that path.

### Run it

```bash
.venv/bin/python -m crickcoach.edge run
```

On the coach screen, **Sessions → Record at the nets** now says the bridge is online and lists the cameras.

### Start it with the computer

The bridge should start whenever the computer does, and the computer must never go to sleep.

- **Linux and Raspberry Pi.** Edit `docs/vansh-edge.service` so its user and folder match yours, then run:

  ```bash
  sudo cp docs/vansh-edge.service /etc/systemd/system/
  sudo systemctl enable --now vansh-edge
  journalctl -u vansh-edge -f        # watch what it is doing
  ```

- **macOS.** In System Settings, turn off sleep when the display is off. Then keep it running in Terminal with `caffeinate -i .venv/bin/python -m crickcoach.edge run`, or load `docs/vansh-edge.plist` as a launch agent; the file explains how.
- **Windows.** Set the power plan to never sleep. Then in Task Scheduler, create a task that runs at log on:
  - **Program:** `C:\vansh\.venv\Scripts\python.exe`
  - **Arguments:** `-m crickcoach.edge run`
  - **Start in:** `C:\vansh`

### The bowling machine (optional)

If a bowling machine sits on the same network, the bridge can bowl the drills the coach queues:

```bash
.venv/bin/python -m crickcoach.edge machine --machine 192.168.1.50:8520 --armed
```

The server needs `VANSH_MACHINE=edge`, and the machine must speak the protocol in the [README](../README.md#bowling-machine-protocol). A bowling machine throws a hard ball at a person, so the bridge has three safeguards:

- **Armed by hand.** It won't bowl unless started with `--armed`.
- **A countdown.** It counts down before every ball.
- **A press of Enter.** By default it waits for Enter at the nets before each ball.

## 6. Calibrate each camera

Calibration tells VANSH where the pitch is in each camera's picture. Do it once per camera position, and again whenever a camera is moved or knocked.

1. Record or upload a short take, then open it under **Sessions**.
2. Pick the camera, then pause on a picture where nobody stands in front of the stumps.
3. Press **Calibrate this camera** and click the points in order. A magnifier follows the pointer.

**Side-on camera.** Click the foot of the stumps at the bowler's end, then the top of those stumps, then the foot and the top at the batter's end. Then type in the camera's distance from the stump line.

![The side-on view, with the four clicks: the foot and then the top of the stumps at the bowler's end, then the foot and the top at the batter's end.](img/side-clicks.jpg)

**Camera behind the bowler.** Click the four places where a popping crease meets a return crease. Start at the bowler's end on the left of the picture, which is the off side for a right-hander. Then click the bowler's end on the right, the batter's end on the right, and the batter's end on the left.

![The view from behind the bowler, with the four crease corners numbered in click order.](img/behind-clicks.jpg)

Click where the white lines cross, and where the stumps meet the ground rather than their shadows. VANSH checks the clicks for order and spread, and keeps the calibration for the lane, so every later session uses it.

## 7. A session at the nets

1. **Check the bridge.** Under **Sessions → Record at the nets**, it should be online with both cameras listed.
2. **Start.** Type a name, such as the batter or bowler, then choose bowling or batting and the batter's hand. Press **Start recording**.
3. **Bowl.** A take arrives every 90 seconds and its balls are measured within a minute or two. Each ball shows where it pitched, its pace, and a stumps call on the pitch map. The takes are grouped under the recording's name.
4. **Stop.** Press **Stop recording** at the end. The last take is sent.
5. **Review.** Mark how the batter played each ball: left, defended, attacked, missed, edged or bowled. **Queue the next drill** finds the zone where the batter was beaten most often in the whole recording and queues six balls for the machine.

You can also upload files from a phone or a recorder under **Sessions → Upload a net session**.

## 8. Check the numbers

VANSH measures with geometry and physics, and every reading says how sure it is. Check it at your own net before you rely on it:

- **Length.** Put cones at 6 m and 8 m from the batter's stumps. Bowl at them and compare the pitch map.
- **Line.** Put a ball-sized marker in front of off stump, and check that balls landing on it show on off stump.
- **Pace.** Compare 20 balls with a radar gun.

These results come from rendered nets with known answers: real physics, moving players, CCTV blur, noise and video compression, and calibration clicks off by 1.5 pixels, as a coach's are. They are medians over 12 balls per row. They have not yet been checked against real footage.

| Camera | Ball, frame rate, shutter | Length band right | Length error | Pace error | Line error |
|---|---|---|---|---|---|
| Side-on, 12 m | red, 25 fps, 1/250 s | 12 of 12 | 0.14 m | 4.9 km/h | – |
| Side-on, 12 m | white, 25 fps, 1/250 s | 11 of 12 | 0.11 m | 5.5 km/h | – |
| Side-on, 12 m | red, 50 fps, 1/500 s | 11 of 12 | 0.17 m | 2.9 km/h | – |
| Side-on, 12 m | red, 25 fps, 1/60 s | 0 of 12 | – | – | – |
| Behind, 8 m back, 6 m up | white, 50 fps, 1/500 s | 11 of 12 | 0.12 m | – | 2.9 cm |
| Behind, 8 m back, 6 m up | red, 25 fps, 1/250 s | 5 of 12 | 1.5 m | – | 11.7 cm |

Two lessons come out of these numbers:

- **The shutter decides everything.** At 1/60 s, VANSH found the length on none of the balls.
- **The camera behind the bowler needs a white or pink ball at 50 frames a second.** A red ball at 25 frames a second is too faint from 6 m up.

## 9. When something is wrong

| What you see | Likely cause | What to do |
|---|---|---|
| Balls are not found, or "found by motion" | The shutter is too slow, or the camera isn't calibrated | Set 1/500 s; calibrate the camera |
| Lengths are wrong by a metre or more | The calibration clicks are off, or the camera has moved | Calibrate again, using the magnifier |
| Pace is wrong by more than 5 km/h | The distance from the stump line is missing or wrong | Enter it when calibrating the side camera |
| "Line not seen" | No camera behind the bowler, or it isn't calibrated | Add and calibrate the camera behind |
| "The ball was hidden before it pitched" | The bowler's follow-through blocked the camera behind | Mount it higher; 6 m is the aim |
| The bridge shows as offline | The computer is asleep or off the network, or the coach key is wrong | Check power and sleep settings, the network, and `vansh-edge.env` |
| "Nothing answered at that address" | Wrong IP address or port, or RTSP is off | Check the camera's address and turn RTSP on |
| "The camera refused the user name or password" | Wrong login, or a symbol in the password | Rebuild the address with the builder |
| "The camera has no stream at that path" | The pattern is wrong for your brand | Try the other patterns, or check the manual |
| Takes are waiting to upload | The upload is slower than the cameras' bitrate | Lower the bitrate to 4 Mbit/s, or let them catch up after the session |
| The picture pulses or shows bands | Flickering floodlights | Use flicker-free lights |
| The video won't play in the browser | The camera sends H.265 | VANSH converts it; set the camera to H.264 to skip that step |

## 10. Privacy and safety

- **Consent.** Tell players, and the parents of anyone under 18, that the net is recorded, why, and how long video is kept. Get written consent for minors.
- **The coach key.** Sessions and video are open only with it. To sign everyone out, change it in `/etc/vansh/vansh.env` on the server and restart VANSH.
- **Storage.** VANSH keeps the newest video within its space, 20 GB by default. When it needs room it removes the oldest videos first; their measurements, outcomes and cues stay. Delete a session to remove its video at once.
- **The bridge's file.** `vansh-edge.env` holds camera passwords and the coach key. Keep it on the bridge computer only.
- **Machine safety.** Follow the three safeguards in section 5.
