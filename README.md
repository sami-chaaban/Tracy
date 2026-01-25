## Tracy

> **Note:** Tracy is currently in **beta** — features and workflows may change regularly.

![Tracy Interface Overview](Screenshots/Interface-Example.png)

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
   7. [Saving](#saving)
3. [Plots](#plots)
   * [Pixel Intensity Histogram](#pixel-intensity-histogram)
   * [Intensity Plot](#intensity-plot)
   * [Speed Histogram](#speed-histogram)
4. [Additional Features](#additional-features)
   * [Drift Correction](#drift-correction)
   * [Colocalization](#colocalization)
   * [Step Finding](#step-finding)
   * [Diffusion](#diffusion)
   * [Reference Image](#reference-image)
   * [Custom Columns](#custom-columns)
   * [Color by Value](#color-by-value)
5. [Loading & Saving](#loading--saving)
   * [Load Trajectories](#load-trajectories)
   * [Save Trajectories](#save-trajectories)
   * [Save Kymographs](#save-kymographs)
   * [Import TrackMate Data](#import-trackmate-data)
6. [License](#license)

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

1. Click **LOAD** (or use **Load » Movie**).
2. Select a single- or multi-channel TIFF movie.
3. If necessary, enter pixel size and frame interval when prompted.
4. Pan by holding down the middle button (or `Ctrl/Cmd`) and dragging, zoom with the mouse wheel.
5. If available, switch channels by clicking the channel label (shortcut: `1`, `2`, …).
   If the wrong axis is treated as channels (or the channel list looks off), use **Movie » Change Channel Axis** to select the correct axis (only enabled for 4D movies).
6. Toggle the maximum projection with the button below the movie (shortcut: `m`).
7. Adjust the contrast using the slider.
8. The kymograph view has its own contrast slider and reset button below it; contrast is remembered per kymograph.
8. **View » Invert** toggles the display colormap (default on) so bright spots appear dark on a light background.

Other load options are available under **Load** — see [Loading & Saving](#loading--saving) for details.

### 3. Browsing Spots <a name="browsing-spots"></a>

1. Click on the movie to detect a spot at the click location.

   * **Blue square:** search region.
   * **Magenta circle:** fitted spot (radius = 2σ).
2. Hover over the inset to view a 3D fit (scroll to zoom, drag to rotate).
3. Hold `r` + scroll (or use **Spot » Search Radius**) to adjust the search radius.
4. Navigate frames with the slider under the movie.
5. The pixel intensity histogram updates for the current search window; see [Pixel Intensity Histogram](#pixel-intensity-histogram) for details.

> The **spot histogram** shows intensities in the search area and highlights values in the spot.
> Inset size can be changed under **View » Inset size** (or right-click inset); this only affects visualization and does not change calculations.
> Use **View » Inset** to show or hide the inset panel (or right-click inset).

### 4. Generating Kymographs <a name="generating-kymographs"></a>

1. Switch to **KYMO** mode (toggle under the movie or `n`).
2. (Optional) To help guide line placement, toggle max‑projection (`m`) or load a [reference image](#reference-image).
3. Draw a segmented line by clicking anchors (press `Esc` to cancel).
4. Double‑click to finish and generate the kymograph.
5. For multi‑channel movies, a kymograph is generated for each channel (toggle with `1`, `2`, …).
6. Cycle through kymographs with `,` (previous) and `.` (next).
7. Use the **Invert** button (left of the kymograph delete button) to horizontally flip the kymograph. This also reverses the line direction and mirrors any kymograph anchors so overlays stay aligned.

> *Tip:* Use the **FILTER** button (to the right of **Anchors**) to toggle a LoG‑filtered view of the current kymograph. The filtered view has its own contrast settings and does not change any tracking/analysis.

### 5. Generating Trajectories <a name="generating-trajectories"></a>

#### A. From a Kymograph

1. On the kymograph, draw a segmented line (blue [anchors](#anchors)) to follow the track.
2. Double‑click to finish and compute a trajectory using your search radius and tracking mode (see [tracking options](#tracking-options)).
3. Click any kymograph or plot point to jump to that spot; use `→`/`←` to step forward and backward.
4. Press spacebar to animate the trajectory and again to stop the playback.
5. If you want to recalculate the trajectory with new tracking options, press `Enter` (or **Trajectory » Recalculate**).
6. If a spot looks wrong, press `x` to invalidate the highlighted point; press `x` again to re‑fit and restore it.

> Optionally connect gaps via **Kymograph » Connect Spot Gaps**.

#### B. Direct Movie Tracking

1. Click an initial spot on the movie.
2. Skip a few frames and click the next spot. Repeat until the track is covered.
3. End a click sequence with `Enter` or cancel with `Esc`.
4. Browse, playback, edit tracks as above.
5. If you want to recalculate the trajectory with new [tracking options](#tracking-options), press `Enter` (or **Trajectory » Recalculate**).

> You can use “video‑game” controls: `w`/`a`/`s`/`d` to move the cursor, `l`/`j` to change frames, `k` to select the spot.

<details>

<summary>Tracking Options <a name="tracking-options"></a></summary>

* **Search Radius:** adjust with `r` + scroll
* **Tracking Mode** (`t`)**:**
  * **Independent** (default)**:** fits each frame independently.
  * **Tracked:** uses previous frame’s spot as center.
  * **Smooth:** independent + post‑filter outliers.
* Avoid using spots in existing tracks via **Spot » Avoid previous spots**.
* Tracking options are set for any subsequent analysis. An existing trajectory can be recalculated using the currently set options by pressing `Enter` (or **Trajectory » Recalculate**).

</details>

<details>

<summary>Anchors <a name="anchors"></a></summary>

* The blue circles are anchor points: direct kymograph clicks, or movie‑click anchors projected onto the current kymograph. Anchors are only shown when the selected trajectory belongs to the currently displayed kymograph/geometry.

* To edit anchors, hold **Shift**. Only the selected trajectory’s dotted line and blue anchor circles are shown. Drag any circle to move that anchor; the dotted line updates live. Release **Shift** to exit edit mode and recalculate if anchors changed. Shift also cancels any active left‑click sequence. Use the **ANCHORS** toggle below the kymograph to hide/show anchor overlays.
* While holding **Shift**, you can **right‑click** an anchor to remove it, or right‑click the dotted line to insert a new anchor. (Tracy keeps at least two anchors and will warn if an insertion breaks anchor order.)

* Anchors define track segments (i.e. between anchors), which allow for per-segment analysis during saving see [Loading & Saving](#loading--saving) as well as per-segment diffusion analysis when requested.

</details>

### 6. Browsing Trajectories <a name="browsing-trajectories"></a>

* New trajectories append to the **Trajectory Table**.
* Click trajectories in the table or use the arrow keys (**`↑`**, **`↓`**). Right‑click for options (e.g. Go to kymograph).
* Delete a trajectory with `Backspace`.
* Right‑click menu options include saving selected trajectories, jumping to the matching kymograph, and marking/setting custom columns.

### 7. Saving <a name="saving"></a>

Use **Save » Trajectories** (`Ctrl/Cmd+S`) to export your analysis; see [Loading & Saving](#loading--saving) for all save/load options and details.

---

## Plots <a name="plots"></a>

### Pixel Intensity Histogram <a name="pixel-intensity-histogram"></a>

* Histogram of pixel intensities in the current search window (the same square crop used for fitting). Uses 50 bins.
* If the fitted sigma is available, the colored overlay shows pixels within 2σ of the fitted center.
* Dashed lines mark the background level and the fitted peak (background + amplitude), when available.

### Intensity Plot <a name="intensity-plot"></a>

* Per‑frame integrated intensity (1‑indexed frames) with average (grey dashed) and median (magenta dashed) lines.

<details>

<summary>Intensity Calculation <a name="intensity"></a></summary>

* **Search window:** each frame is fit in a square crop of size `2 * Search Radius` centered on the per‑frame search center (interpolated between anchors in Independent/Smooth; updated from the previous fit in Tracked).
* **Model:** 2D Gaussian + constant offset (background). If a fixed trajectory background exists, the offset is held fixed; otherwise it is fitted per frame.
* **Fixed trajectory background:** computed once per trajectory by sampling the outer 10% border pixels of each frame’s crop (only from non‑truncated edges) and taking the median across all frames. This is the value reported as “Background from trajectory”.
* **Fit constraints:** center is constrained to within ±4 px of the crop center; sigma is bounded between a conservative minimum based on a 200 nm PSF FWHM converted via `FWHM = 2.355·σ`, multiplied by 2 (scaled by pixel size, or 1 px if unset), and a maximum of `crop_size / 4`. The lower bound suppresses spuriously sharp fits; the upper bound prevents overly broad, background‑dominated fits without requiring NA/wavelength inputs.
* **Quality checks:** fits are rejected if the crop has low contrast (max‑median < 4×std), if the initial amplitude is too small, or if the fitted center lands within 4 px of the crop edge. These filters match common single‑molecule practice for suppressing low‑SNR or edge‑biased localizations.
* **Two‑pass fitting:** the fit is run twice (recrop around the fitted center) to improve accuracy.
* **Outputs:** Spot Center = fitted center; Sigma = mean of σx and σy; Peak = fitted amplitude A; Intensity = integrated Gaussian `2π * A * σx * σy` (clamped to ≥0); Background = fitted offset (or fixed background, clamped to ≥0).
* **Smooth mode:** after independent fits, centers are Savitzky‑Golay smoothed; frames deviating by more than `min(3 px, 2×mean σ)` are re‑fit at the smoothed center with a crop radius of ~`4×mean σ`.

</details>

* The top strip mirrors the per‑frame color coding (e.g. Color By or colocalization), and missing/invalid intensities appear grey.
* When step‑finding is enabled, step medians and transitions are overlaid in green.
* Click a point to jump to that frame.

### Speed Histogram <a name="speed-histogram"></a>

* Histogram of frame‑to‑frame speeds in px/frame, or μm/s when pixel size + frame interval are set.
* Dashed vertical lines show the average speed and the net speed (start‑to‑end displacement divided by total time).

---

## Additional Features <a name="additional-features"></a>

### Drift Correction <a name="drift-correction"></a>

1. Identify a stationary reference spot that can be found in most frames.
2. Click it and ensure it is found (i.e. a magenta circle appears). It does not matter which frame you choose.
3. Use **Movie » Correct Drift** to track it from start‑to‑end and apply the frame shifts.
4. Review and save if satisfactory.

### Colocalization <a name="colocalization"></a>

* Determines colocalization if a spot exists within 4 pixels in the other channel.
* Enable via **Trajectory » Calculate Colocalization** for multi‑channel movies.
* If existing trajectories are missing colocalization values, Tracy will prompt to calculate them; choosing **No** leaves them uncalculated (the toggle stays on for future trajectories).
* Results appear as new table columns.

### Step Finding <a name="step-finding"></a>

* Calculates steps in the intensity profile.
* Enable **Trajectory » Calculate Steps**.
* The settings dialog uses **Set**/**Cancel**; **Set** applies to future calculations.
* Adjust rolling‑average window and minimum step size:

  * **Rolling average window**: the smoothing window size (**W**) applied to the intensity trace before detecting steps. Larger values smooth noise more strongly but can blur short-lived steps; smaller values preserve fast changes but may be noisier. The window is in **data points/frames** (after invalid points are removed).
  * **Minimum step size**: the **minimum intensity change** required to accept a step edge. Increase this to ignore small/noisy fluctuations; decrease it to detect smaller steps. This threshold uses the same units as the intensity values shown in the plot.
* If existing trajectories are missing step data, Tracy will prompt to calculate them; choosing **No** leaves them uncalculated (the toggle stays on for future trajectories).
* Results appear as steps in the **Intensity Plot**.
* Detected steps and sizes are saved in **Per-trajectory** sheet; each point’s step ID in **Data Points**.

### Diffusion <a name="diffusion"></a>

* Estimates **anomalous diffusion** parameters from the trajectory’s mean-squared displacement (MSD):

  * **MSD(Δt) = 4D · (Δt)^α** (2D)

* Rationale: this is the standard 2D MSD power-law used in single-particle tracking, with **α = 1** for Brownian motion and **α ≠ 1** capturing anomalous diffusion. Tracy uses the imaging plane (x–y), so the 2D prefactor (4) is appropriate for planar motion; if your trajectories are strictly along a filament, a 1D model would use a prefactor of 2, which would scale the reported **D** by about 2. The fit is intentionally simple (no explicit offset term), so localization error or mixed directed/diffusive motion can bias **D** and **α**; in those cases, use shorter lags or interpret values as effective parameters.

* Enable via **Trajectory » Calculate Diffusion**.
* Two analysis parameters control the MSD fit window:

  * **Max lag:** the largest time separation (Δt) included when computing MSD points and fitting **D** and **α**. Larger values include longer time scales but use fewer displacement pairs (and can be noisier).
  * **Min pairs per lag:** the minimum number of displacement pairs required to accept a given lag. If fewer pairs are available (e.g. short tracks or many invalid points), that lag is skipped.
* Requires **pixel size** and **frame interval** to be set (units: **μm²/s** for **D**, unitless for **α**). If either is missing, diffusion cannot be computed.
* If existing trajectories are missing diffusion values, Tracy will prompt to calculate them; choosing **No** leaves them uncalculated (the toggle stays on for future trajectories).
* Results appear as new trajectory table columns (e.g. **D (μm²/s)** and **α**).
* You can also **Color By** diffusion outputs (e.g. by **α** ranges) under **Trajectories » Color By**.

### Reference Image <a name="reference-image"></a>

* Useful for overlaying filaments or guides during kymograph creation.
* Load via **Load » Reference Image**
* Toggle with the **REF** icon under the movie.
* While toggled, use `Ctrl/Cmd` + arrows to nudge the reference image if necessary

### Custom Columns <a name="custom-columns"></a>

* Right‑click any column header or use **Trajectories » Add Column**.

  * **Binary:** Yes/No flags.
  * **Value:** any numeric/text value.
* Assign via right‑click on table row or kymograph label.

### Color by Value <a name="color-by-value"></a>

* If custom column or colocalization data exists, under **Trajectories » Color By** choose binary, value, or colocalization.
* **Color By** appears only when there is something available to color by.
* When diffusion is enabled, **Color By** also offers **D** / **α** options for **per‑segment** coloring (uses segment diffusion values).

---

## Loading & Saving <a name="loading--saving"></a>

Use the **Load** and **Save** menus to move data in and out of Tracy.

**Load menu options:**
* **Movie**: single- or multi-channel TIFF movie.
* **Line ROIs**: ImageJ-generated `.roi` or `.zip` file containing line ROIs.
* **Trajectories**: Tracy trajectory `.xlsx` file (see [Load Trajectories](#load-trajectories)).
* **Reference**: single-frame TIFF image used as an overlay (see [Reference Image](#reference-image)).
* **TrackMate spots**: TrackMate-generated `.csv` spot file (see [Import TrackMate Data](#import-trackmate-data)).

**Save menu options:**
* **Trajectories**: exports a `.xlsx` workbook with all trajectory data (details below).
* **Kymographs**: exports selected kymograph images (with optional overlays).
* **Line ROIs**: exports an ImageJ ROI `.zip` that can be reopened in ImageJ later.

### Load Trajectories <a name="load-trajectories"></a>

* Load `.xlsx` files with the above sheets or similar formats (requires columns: Trajectory, Channel, Frame, Search Center X, Search Center Y).
* If the workbook includes kymograph geometry, Tracy can regenerate kymographs from the `Kymo-Anchors`/`ROI` columns, and it can optionally restore empty kymographs recorded in **Per-kymograph**.
* Use **Kymograph » Draw from Trajectories** to redraw the stored kymograph lines saved in the spreadsheet (Per-trajectory `Kymo-Anchors`/`ROI` columns).

### Save Trajectories <a name="save-trajectories"></a>

* **Save » Trajectories** (`Ctrl/Cmd+S`) exports an Excel workbook with five sheets:

  1. **Aggregate Analysis**: a single-row summary across the whole movie.
  2. **Data Points**: per-frame spot measurements along each trajectory.
  3. **Per-trajectory**: one-row summary statistics for each trajectory.
  4. **Per-segment**: one-row summary for each trajectory segment between consecutive anchors.
  5. **Per-kymograph**: aggregates grouped by kymograph geometry.

<details>
<summary>Sheet: Aggregate Analysis</summary>

A single row summarizing the whole movie.

##### Columns

* **Tracy Version**: version of Tracy used to generate the export.
* **Pixel size (nm/px)**: pixel size used for unit conversions (blank if unknown).
* **Frame time (ms)**: frame interval used for unit conversions (blank if unknown).
* **Total movie frames**
* **Total time (s)**: total movie duration (blank if frame time unknown).
* **Movie dimensions (px)**: `width, height`.
* **Movie dimensions (μm)**: `width, height` converted using pixel size (blank if unknown).
* **Total kymographs**: number of kymograph geometries (see multi-channel notes below).
* **Summed kymograph distances (μm)**: sum of all kymograph geometry lengths (includes empty kymographs; blank if pixel size unknown).
* **Empty kymographs**: kymographs with zero trajectories.
* **Number of trajectories**
* **Number of events (/min)**: total trajectories per total movie time in minutes (blank if frame time unknown).
* **Number of events (/um/min)**: events per minute divided by total kymograph distance (blank if pixel size or frame time unknown).
* **Average net speed (μm/s)**, **Average average speed (μm/s)**, **Average run length (μm)**, **Average run time (s)**,
  **Average median intensity**, **Average average intensity**: means across all trajectories, regardless of kymograph.

</details>

<details>
<summary>Sheet: Data Points</summary>

Each row is one frame from one trajectory.

##### Columns

* **Trajectory**: trajectory ID.
* **Channel**: movie channel the trajectory was tracked in.
* **Clicks**: source of the anchor sequence (`kymograph` or `movie`).
* **Trajectory Segment**: segment index for the current frame (segment stays on the end‑anchor frame; empty if no anchors).
* **Frame**: 1-indexed frame number.
* **Original Coordinate X / Y**: the original (raw) coordinate for that frame.
* **Search Center X / Y**: the search center used for tracking in that frame.
* **Spot Center X / Y**: fitted spot center for that frame (blank if fit failed).
* **Intensity**: integrated spot intensity (blank if invalid/missing).
* **Sigma**: fitted spot σ (blank if fit failed).
* **Peak**: fitted peak amplitude (blank if fit failed).
* **Background from trajectory**: `Yes` if a fixed background was used for the trajectory, else `No`.
* **Background**: per-frame background estimate (blank if not computed); when “from trajectory”, the fixed background is computed from border pixels across the full trajectory and then applied to all frames.
* **Speed (px/frame)**: frame-to-frame speed in pixels.
* **Speed (μm/s)** / **Speed (μm/min)**: speed converted using pixel size + frame interval (blank if either is missing).

##### Optional columns: step finding

> Only present if step-finding is enabled and steps exist.

* **Step Number**: step segment index for that frame.
* **Step Intensity Value**: median intensity for that step segment.
* **Step Intensity Value (background-adjusted)**: step median minus the per-frame background (when available).

##### Optional columns: colocalization

> Only present for multi-channel movies when colocalization is enabled.

* **Colocalized w/any channel**: `Yes`/`No` per frame (blank if not evaluated).
* **Colocalized w/ch1**, **Colocalized w/ch2**, …: `Yes`/`No` per frame for each channel (the reference channel column is left blank).

</details>

<details>
<summary>Sheet: Per-trajectory</summary>

Each row is one trajectory.
If `Kymo-Anchors` and `ROI` are present, Tracy can regenerate the kymograph locations on load.

##### Columns

* **Movie**: movie file name.
* **Trajectory**: trajectory ID.
* **Channel**: channel the trajectory was tracked in.
* **Start Frame / End Frame**: 1-indexed start/end frames.
* **Kymo-Anchors**: JSON list of kymograph anchors (frame index + kymo x/y in px; frame index is Tracy’s internal index).
* **Kymograph geometry** (`ROI` column): JSON description of the kymograph line.
* **Clicks**: source of the anchor sequence (`kymograph` or `movie`).
* **Movie-Anchors**: JSON list of anchors in movie coordinates as `(x, y, frame)`; frame is 1‑indexed.
* **Segments**: number of segments in the trajectory (`Movie-Anchors` minus one).
* **Total Points**: number of frames in the trajectory.
* **Valid Points**: number of frames with a valid intensity (>0).
* **Percent Valid**: `100 * Valid Points / Total Points`.
* **Search Center X Start / Y Start**: starting search center coordinate.
* **Search Center X End / Y End**: ending search center coordinate.
* **Distance (μm)**: straight-line displacement from start→end converted to μm (blank if pixel size missing).
* **Time (s)**: duration from start→end in seconds (blank if frame interval missing).
* **Background**: fixed trajectory background value (blank if not used).
* **Average Intensity**: mean intensity over valid points.
* **Median Intensity**: median intensity over valid points.
* **Net Speed (px/frame)**: straight-line displacement / (end-start frames).
* **Net Speed (μm/s)** / **Net Speed (μm/min)**: net speed converted using pixel size + frame interval (blank if either is missing).
* **Avg. Speed (px/frame)**: mean of per-frame speeds.
* **Avg. Speed (μm/s)** / **Avg. Speed (μm/min)**: average speed converted (blank if either is missing).

##### Optional columns: step finding

> Only present if step-finding is enabled and steps exist.

* **Number of Steps**
* **Average Step Size**: average absolute difference between consecutive step medians.
* **Average Step Size w/Step to Background**: as above, but also includes the final step-to-background difference when a fixed background exists.

##### Optional columns: diffusion

> Only present if diffusion is enabled and could be computed (requires pixel size + frame interval).

* **D (μm²/s)**: diffusion coefficient from MSD fit.
* **α**: anomalous diffusion exponent from MSD fit.

##### Optional columns: custom columns

> Only present if you added custom columns in the UI.

* Custom columns appear as **`Name [binary]`** or **`Name [value]`** depending on the column type.
* You can also add custom columns directly in the **Per-trajectory** sheet using the same **`Name [binary]`** or **`Name [value]`** header format.
* If a custom column header has no **`[binary]`** or **`[value]`** suffix, it is assumed to be **`[value]`** on load.

##### Optional columns: colocalization summary columns

> Only present for multi-channel movies when colocalization columns exist.

* **Ch. 1 co. %**, **Ch. 2 co. %**, …: percent of frames colocalized with each other channel.
  * For 2-channel movies, the non-reference channel column is the overall colocalization percent.
  * For >2 channels, each non-reference column is computed separately per target channel.
  * The reference channel’s own **Ch. X co. %** cell is left blank.

</details>

<details>
<summary>Sheet: Per-segment</summary>

Each row summarizes one segment between consecutive anchors in a trajectory.

##### Columns

* **Movie**: movie file name.
* **Trajectory**: trajectory ID.
* **Segment**: segment index within the trajectory (1‑indexed).
* **Channel**: channel the segment belongs to.
* **Clicks**, **Kymo-Anchors**, **Kymograph geometry** (`ROI` column): same meanings as in Per-trajectory.
* **Segment Start X / Y / Frame**: anchor position for segment start (movie coordinates; frame is 1‑indexed).
* **Segment End X / Y / Frame**: anchor position for segment end (movie coordinates; frame is 1‑indexed).
* All remaining numeric columns mirror **Per-trajectory**, but computed per segment.

</details>

<details>
<summary>Sheet: Per-kymograph</summary>

Each row is one kymograph geometry.
Tracy can optionally include empty kymographs (no trajectories) as blank rows so distance‑normalized stats still account for kymograph lengths; these rows can be restored on load.

##### Columns

* **Kymograph geometry** (`ROI` column): JSON (same format as in Per-trajectory).
* **Total distance (μm)**: total polyline length of the kymograph geometry in μm (blank if pixel size missing).
* **Total time (s)**: total movie duration in seconds (blank if frame interval missing).
* **Number of trajectories**: trajectories whose kymograph geometry matches this JSON.
* **Events (/min)**: `Number of trajectories / (Total time in minutes)` (blank if frame interval missing).
* **Events (/μm/min)**: `Events (/min) / Total distance (μm)` (blank if pixel size or frame interval missing).
* **Average net speed (μm/s)**: mean **Net Speed (μm/s)** across trajectories in this kymograph.
* **Average average speed (μm/s)**: mean **Avg. Speed (μm/s)** across trajectories in this kymograph.
* **Average run length (μm)**: mean **Distance (μm)** across trajectories in this kymograph.
* **Average run time (s)**: mean **Time (s)** across trajectories in this kymograph.
* **Average median intensity**: mean **Median Intensity** across trajectories in this kymograph.
* **Average average intensity**: mean **Average Intensity** across trajectories in this kymograph.

</details>

<details>
<summary>Multi-channel notes</summary>

* **Per-kymograph and Aggregate “kymograph” counts are kymograph-geometry-based, not per-channel.** In multi-channel movies, Tracy draws a kymograph for each channel from the same kymograph geometry, but the export currently groups by kymograph geometry only. That means:
  * **Per-kymograph** rows are kymograph geometries (not separate rows per channel).
  * **Per-kymograph Ch. X** sheets are exported for each channel when you need per-channel analysis.
  * **Total kymographs** counts kymograph geometries (not `kymograph geometry × channels`).
* **Overlapping kymograph geometries / shared trajectories:** if the same trajectory is associated with multiple kymographs, the **Per-kymograph** sheet can double-count trajectories and bias the averages.

</details>

### Save Kymographs <a name="save-kymographs"></a>

* Use **Save » Kymographs** to export one or more kymograph images.
* Choose file type (`tif`, `png`, `jpg`), select a LUT, and optionally overlay trajectories and labels.
* For TIFF exports, the LUT is embedded for ImageJ; for PNG/JPG exports, the LUT is baked into the pixels.

### Import TrackMate Data <a name="import-trackmate-data"></a>

* Load `.csv` from TrackMate (spot file) via **Load » TrackMate spots**; uses `TRACK_ID`, `FRAME`, `POSITION_X`, `POSITION_Y`.
* TrackMate points are treated like movie anchors and recorded with click source `trackmate`.

---

## License <a name="license"></a>

This project is released under the MIT License — see [LICENSE.txt](https://github.com/sami-chaaban/tracy/blob/main/LICENSE.txt).
