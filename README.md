## Tracy (Beta)

> **Note:** Tracy is currently in **beta** — features and workflows may change regularly.

---

### Table of Contents

1. [Installation](#installation)
2. [Getting Started](#getting-started)

   1. [Launching Tracy](#launching-tracy)
   2. [Loading Your Movie](#loading-your-movie)
   3. [Browsing Spots](#browsing-spots)
   4. [Generating Kymographs](#generating-kymographs)
   5. [Generating Trajectories](#generating-trajectories)
   6. [Browsing Trajectories](#browsing-trajectories)
3. [Additional Features](#additional-features)

   * [Drift Correction](#drift-correction)
   * [Colocalization](#colocalization)
   * [Step Finding](#step-finding)
   * [Reference Image](#reference-image)
   * [Custom Columns](#custom-columns)
   * [Color by Value](#color-by-value)
4. [Saving & Loading](#saving--loading)

   * [Save Trajectories](#save-trajectories)
   * [Load Trajectories](#load-trajectories)
   * [Import TrackMate Data](#import-trackmate-data)
5. [License](#license)

---

## Installation <a name="installation"></a>

Install Tracy with your terminal:

```bash
# Create and activate a dedicated environment
conda create -n tracy python=3.10 -y
conda activate tracy

# Install Tracy
pip install tracyspot
```

> *Tip:* On Windows, you can download Anaconda and use the Anaconda Prompt as your terminal.

---

## Getting Started <a name="getting-started"></a>

### 1. Launching Tracy <a name="launching-tracy"></a>

```bash
conda activate tracy       # if not already active
pip install tracyspot --upgrade   # update during beta
tracy &                    # run in the background
```

> *Note:* The first launch may take a few seconds.

### 2. Loading Your Movie <a name="loading-your-movie"></a>

1. Click **Load Movie** (or use **Load » Movie**).
2. Select a single- or multi-channel TIFF movie.
3. If necessary, enter pixel size and frame interval when prompted.
4. Pan with the middle‑button drag (or Ctrl/Cmd + drag), zoom with the mouse wheel.
5. Switch channels by clicking the channel label (shortcut: `1`, `2`, …).
6. Toggle the maximum projection with the button below the movie (shortcut: `m`).
7. Adjust the contrast using the slider.

### 3. Browsing Spots <a name="browsing-spots"></a>

1. Click on the movie to detect a spot at the click location.

   * **Blue square:** search region.
   * **Magenta circle:** fitted spot (radius = 2σ).
2. Hover over the inset to view a 3D fit (scroll to zoom, drag to rotate).
3. Hold `r` + scroll (or use **Spot » Search Radius**) to adjust the search radius.
4. Navigate frames with the slider under the movie.

> The **spot histogram** shows intensities in the search area and highlights values in the spot.

### 4. Generating Kymographs <a name="generating-kymographs"></a>

1. Switch to **Line** mode (slider under movie or `n`).
2. (Optional) Toggle max‑projection (`m`) to guide line placement or load a [reference image](#reference-image).
3. Draw a segmented line by clicking anchors (press `Esc` to cancel).
4. Double‑click to finish and generate the kymograph.
5. For multi‑channel movies, a kymograph is generated for each channel (toggle with `1`, `2`, …).
6. Cycle through kymographs with `,` (previous) and `.` (next).

> *Tip:* Apply a LoG filter via **Kymograph » Apply LoG filter** for clearer tracks (applies to subsequent kymographs).

### 5. Generating Trajectories <a name="generating-trajectories"></a>

#### A. From a Kymograph

1. On the kymograph, draw a segmented line (blue anchors) to follow the track.
2. Double‑click to finish and compute a trajectory using your search radius and tracking mode (see [tracking options](#tracking-options)).
3. Click any kymograph or plot point to jump to that spot; use `→`/`←` to step forward and backward.
4. Press spacebar to animate the trajectory.
5. If you want to recalculate the trajectory with new tracking options, press `Enter` (or **Trajectory » Recalculate**).
6. (Optional) Fill gaps via **Kymograph » Connect Spot Gaps**.

> Trajectories are displayed on kymographs dynamically, so overlapping kymographs may share trajectories.

#### B. Direct Movie Tracking

1. Click an initial spot on the movie.
2. Skip a few frames and click the next spot. Repeat until the track is covered.
3. End a click sequence with `Enter` or cancel with `Esc`.
4. Browse/select tracks as above.
5. If you want to recalculate the trajectory with new [tracking options](#tracking-options), press `Enter` (or **Trajectory » Recalculate**).

> You can use “video‑game” controls: `w`/`a`/`s`/`d` to move the cursor, `l`/`j` to change frames, `k` to select the spot.

#### Tracking Options <a name="tracking-options"></a>

* **Search Radius:** adjust with `r` + scroll
* **Tracking Mode** (`t`)**:**

  * **Independent** (default)**:** fits each frame independently.
  * **Tracked:** uses previous frame’s spot as center.
  * **Smooth:** independent + post‑filter outliers.
* Tracking options are set for any subsequent analysis. An existing trajectory can be recalculated using the currently set options by pressing `Enter` (or **Trajectory » Recalculate**).
* If a spot looks wrong, you can invalidate it with `x` when it's highlighted.
* Avoid using spots in existing tracks via **Spot » Avoid previous spots**.

---

## Plots <a name="plots"></a>

* **Intensity Plot:** integrated spot intensity at each frame.
* **Speed Histogram:** frame‑to‑frame speeds with the net speed overlayed (i.e. only considering start and end position).

---

## Browsing Trajectories <a name="browsing-trajectories"></a>

* New trajectories append to the **Trajectory Table**.
* Click trajectories in the table or use the arrow keys (**`↑`**, **`↓`**) or click; right‑click for options (e.g., Go to kymograph).
* Delete a trajectory with `Backspace`.

---

## Additional Features <a name="additional-features"></a>

### Drift Correction <a name="drift-correction"></a>

1. Identify a stationary reference spot.
2. Ensure it’s detected by clicking it (magenta circle).
3. Use **Movie » Correct Drift** to track it from start‑to‑end and apply the frame shifts.
4. Review and save if satisfactory.

### Colocalization <a name="colocalization"></a>

* Determines colocalization if a spot exists within 4 pixels in the other channel.
* Toggle under **Spot » Colocalization** for multi‑channel movies.
* Existing trajectories prompt analysis
* Results appear as new table columns.

### Step Finding <a name="step-finding"></a>

* Calculates steps in the intensity profile.
* Enable **Trajectory » Calculate Steps**.
* Adjust rolling‑average window and minimum step size.
* Existing trajectories prompt analysis.
* Results appear as steps in the **Intensity Plot**.
* Detected steps and sizes are saved in **Per‑Trajectory** sheet; each point’s step ID in **Data Points**.

### Reference Image <a name="reference-image"></a>

* Useful for overlaying filaments or guides during kymograph creation.
* Load via **Load » Reference Image**
* Toggle with the icon under the movie.

### Custom Columns <a name="custom-columns"></a>

* Right‑click any column header or use **Trajectories » Add Column**.

  * **Binary:** Yes/No flags.
  * **Value:** any numeric/text value.
* Assign via right‑click on table row or kymograph label.

### Color by Value <a name="color-by-value"></a>

* If custom column or colocalization data exists, under **Trajectories » Color By** choose binary, value, or colocalization.

---

## Saving & Loading <a name="saving--loading"></a>

### Save Trajectories <a name="save-trajectories"></a>

* **Save » Trajectories** exports an Excel file with three sheets:

  1. **Data Points**: all spot measurements.
  2. **Per‑Trajectory**: summary statistics per trajectory.
  3. **Per‑Kymograph**: stats grouped by kymograph.

⚠️ If a trajectory can be found within two kymographs, the per‑kymograph stats will be wrong.

### Load Trajectories <a name="load-trajectories"></a>

* Load `.xlsx` files with the above sheets or similar formats (requires columns: Trajectory, Channel, Frame, Search Center X, Search Center Y).
* Use **Kymograph » Draw from Trajectories** to redraw embedded lines.

### Import TrackMate Data <a name="import-trackmate-data"></a>

* Load `.csv` from TrackMate via **Load Trajectories**; uses `TRACK_ID`, `FRAME`, `POSITION_X`, `POSITION_Y` to perform a search.

---

## License <a name="license"></a>

This project is released under the MIT License — see [LICENSE.txt](https://github.com/sami-chaaban/tracy/blob/main/LICENSE.txt).
