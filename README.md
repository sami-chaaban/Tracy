# Tracy

***Tracy is still in beta***

1. [Install](#install)
2. [Getting started](#start)
    1. [Start Tracy](#starttracy)
    2. [Load your movie](#loadmovie)
    3. [Browse spots](#browsespots)
    4. [Generate kymographs](#kymographs)
    5. [Generate trajectories](#trajectories)
    6. [Browse trajectories](#browsetrajectories)
    7. [Modify points](#modifypoints)
3. [Additional features](#features)
    * [Drift correction](#driftcor)
    * [Colocalization](#colocalization)
    * [Step finding](#stepfinding)
    * [Custom column](#customcolumns)
    * [Color by value](#coloring)
4. [Save & Load](#saveload)
    * [Save trajectories](#savetrajectories)
    * [Load trajectories](#loadtrajectories)
    * [Load TrackMate data](#loadtrackmate)
5. [License](#license)

## Install<a name="install"></a>

***Hopefully this will not involve the terminal in the future***

1. Open your terminal
    * Mac: just open Terminal
    * Windows: install Anaconda and use the Anaconda prompt as the terminal
2. `conda create -n tracy python=3.10 -y`
3. `conda activate tracy`
4. `pip install tracyspot`

## Getting started<a name="start"></a>

### Start Tracy<a name="starttracy"></a>

1. Open your terminal
2. `conda activate tracy` *if not already active*
3. `pip install tracyspot --upgrade`  *always update while tracy is in beta*
4. **`tracy &`**  *it might be slow to open the first time*

### Load your movie<a name="loadmovie"></a>

1. Click **`Load Movie`** (also in the **Load** menu)
2. Choose any single- or multi-channel movie (.tif format)
3. If the pixel size and frame interval could not be loaded from the metadata, you will be prompted to enter them
4. Click and drag using the middle button to pan (or Ctrl + left click / Cmd + left click) and middle wheel scroll to zoom
5. Once loaded, toggle between channels by clicking the channel label (shortcut: **`1`**, **`2`**, etc.)
6. The maximum projection can be shown by toggling it under the movie  (shortcut: **`m`**)
7. Adjust the contrast using the contrast slider

### Browse spots<a name="browsespots"></a>

1. **Click** on the movie to find a spot using the click position as the search center
    * The **blue square** marks the search area around the center
    * The **magenta circle** marks the spot that was found *(the circle radius is 2 standard deviations)*
2. Hover over the top-right inset to see a 3D representation of the fit (scroll to zoom, click and drag to rotate)
3. You can modify the **search radius** by holding down the **`r`** key and scrolling (or in the **Spot** menu)
4. Use the slider under the movie to browse frames

### Generate kymographs<a name="kymographs"></a>

1. To generate a kymograph, enter **`Line`** mode using the switch under the movie (shortcut: **`n`**)
2. Optionally, look at the maximum-projection (shortcut: **`m`**) to better see where you should draw lines
3. Draw the segmented line by placing green anchors on the movie (**`Escape`** cancels a sequence)
4. Double-click to complete the sequence and generate a **kymograph**
5. If your movie has multiple channels, a kymograph will be generated for each, which will show when you toggle between channels (shortcut: **`1`**, **`2`**, etc.)
6. You can use the **`,`** and **`.`** keys to quickly go to the previous or next kymograph, respectively

### Generate trajectories<a name="trajectories"></a>

1. To track a spot in the movie using the kymograph as a reference, draw a segmented line by placing blue anchors on the kymograph (**`Escape`** cancels a sequence)
2. Double-click to complete the sequence to generate a **trajectory**, where spot centers are searched in the movie from the linear interpolation between clicks using the currently set **search radius** and **tracking mode** (see below)
3. Optionally, press the **space bar** to play a movie of the trajectory
4. Assess the plots
    * The **spot histogram** shows the pixel intensities in the search range around the spot center and highlights the intensities within the spot
    * The **intensity plot** shows each spot's integrated intensity
    * The **speed histogram** shows the frame-to-frame speeds of the spots and overlay sthe net speed (only considering the start and end point)
5. If necessary, modify the **search radius** (hold down **`r`** and scroll) and recalculate the trajectory (**`Enter`** key or in the *Trajectory* menu)
6. If necessary, toggle between **tracking mode** with the **`t`** key and recalculate the trajectory (**`Enter`** key or in the *Trajectory* menu)
    * **Independent**: each frame is treated independently using the search center from the interpolated line
    * **Tracked**: each frame's search center is based on the previous frame's spot center
    * **Smooth**: equivalent to *Independent* mode but goes through a filtering step at the end to remove spots that are far off the main track
7. Click any point in the kymograph or in the *intensity plot* to jump to that point, or browse sequentially with the arrow keys (**`→`**, **`←`**)

    **!!** A trajectory's presence within a kymograph is determined on-the-fly for visualisation, so overlapping kymographs can show the same trajectory

### Browse trajectories<a name="browsetrajectories"></a>

* Adding trajectories will append the data to the **trajectory table**, which can be clicked or selected with the arrow keys (**`↑`**, **`↓`**)
* Right click a trajectory in the table to show some helpful options, like **Go to kymograph ch1-001**
* The **`backspace`** key removes the selected trajectory(ies)

### Modify points<a name="modifypoints"></a>

* When a point is highlighted, use the **`X`** key to either invalidate the spot or re-attempt a fit if it is already invalid

## Additional features<a name="features"></a>

### Drift correction<a name="driftcor"></a>

1. If your movie drifts, find a spot that is present throughout the movie and is stationary
2. Click the spot (any frame) and make sure it has been found (magenta circle)
3. In the **Movie** menu, click **`Correct Drift`**, which will track the spot to the beginning and end of the movie and apply a shift to each frame accordingly
4. Check the result in the pop-up and save/load the movie if acceptable

### Colocalization<a name="colocalization"></a>

* If your movie has multiple channels, colocalization with other channels can be toggled on under the *Spot* menu
* If trajectories are already available that have not had their colocalization analysed, it will prompt to analyse them
* New columns will show up in the table representing the percentage of spots within a trajectory that are colocalized
* As long as the colocalization option is toggled, every subsequent trajectory will have its colocalization analysed
* Colocalization is determined by performing a search using the same spot center coordinates but in a different channel, and marking as colocalized if a spot is found within 4 pixels of the original spot center coordinate
* Colocalisation data will appear in the saved trajectory file

### Step finding<a name="stepfinding"></a>

* If your spot intensity increases or decreases in a step-like fashion, such as with bleaching data, you can analyse the steps by toggling *Calculate Steps* in the *Trajectory* menu
* If trajectories are already available that have not had their steps analysed, it will prompt to analyse them
* The dialog box allows you to modify the *Rolling average window* and *Minimum step size*
* As long as the step option is toggled, every subsequent trajectory will have its steps analysed
* Steps are found by smoothing the noisy intensity trace using the rolling average window to reveal the underlying trend, and jumps exceeding the minimum step size are detected, where segments between jumps take on the median intensity
* Number of steps and average step size will appear in the Per-trajectory sheet of the saved trajectories, in addition to each data point's step identity in the Data Points sheet

### Custom columns<a name="customcolumns"></a>

* Right click on any column header to show the options for column types to add (also in the *Trajectories* menu)
    * **Binary column**: adds a column composed of Yes/No, which can be assiged to each trajectory
    * **Value column**: adds a column that can hold any value, which can be assiged to each trajectory
* After adding a column, right click on a trajectory in the table or on any trajectory label in the kymograph to either **Mark as ***X***** or **Set ***X*****, respectively
* Optionally, colour by this value (see [below]<a name="coloring"></a>)

### Color by value<a name="coloring"></a>

* All data can be coloured by **binary**, **value**, or **colocalization** values under the *Trajectories* menu

## Save & Load<a name="saveload"></a>

### Save trajectories<a name="savetrajectories"></a>

* Save trajectories in the **Save** menu, which saves an excel file with three sheets
    * **Data points**: All spots and their corresponding data
    * **Per-trajectory**: Data corresponding to individual trajectories
    * **Per-kymograph**: Analysis of points belonging to the same kymograph

    **!!** Make sure two kymographs do not contain the same trajectory or the Per-kymograph statistics will be wrong

* You do not need to save kymographs or line ROIs along with trajectories since the clicks you used to build them are embedded in the trajectories. Use **Draw from trajectories** in the *Kymograph* menu to redraw them after loading trajectories.

### Load trajectories<a name="loadtrajectories"></a>

* Trajectories can be loaded back as they were saved by Tracy (.xlsx file) or any similar file with a Data Points sheet with at least Trajectory, Channel, Frame, Search Center X, and Search Center Y (it will recalculate trajectories when spot centers are missing in this case)
* You do not need to save kymographs or line ROIs along with trajectories since the clicks you used to build them are embedded in the trajectories. Use **Draw from trajectories** in the *Kymograph* menu to redraw them after loading trajectories.

### Load TrackMate data<a name="loadtrackmate"></a>

* TrackMate data (.csv) can be loaded using the same *Load trajectories* option, which will trigger a calculation using the TrackMate spot data ("TRACK_ID", "FRAME", "POSITION_X", "POSITION_Y") as search centers, generating a Tracy trajectory for each TrackMate track.

## License<a name="license"></a>

This project is licensed under the MIT License - see the [LICENSE.txt](https://github.com/sami-chaaban/tracy/blob/main/LICENSE.txt) file for details.
