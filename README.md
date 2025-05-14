# Tracy

1. [Install](#install)
2. [Getting started](#start)
    * [Start Tracy](#starttracy)
    * [Load your movie](#loadmovie)
    * [Browse spots](#browsespots)
    * [Drift correction](#driftcor)
    * [Kymographs](#kymographs)
    * [Track using kymograph reference](#kymoclick)
    * [Modify individual points](#modify)
    * [Browse trajectories](#browsetrajectories)
3. [License](#license)

## Install<a name="install"></a>

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
4. Once loaded, toggle between channels with the number keys
5. Use the **`M`** shortcut to show/hide the maximum projection
6. Adjust the contrast using the contrast slider

### Browse spots<a name="browsespots"></a>

1. **Click** on the movie to find a spot using the click position as the search center
    * The **blue square** marks the search area around the center
    * The **magenta circle** marks the spot that was found *(the circle radius is 2 standard deviations)*
2. Hover over the top-right inset to see a 3D representation of the fit (scroll to zoom, click and drag to rotate)
3. You can modify the **search radius** by holding down the **`R`** key and scrolling (or in the **Spot** menu)
4. Use the slider under the movie to browse frames

### Drift correction<a name="driftcor"></a>

1. If your movie drifts, find a spot that is present throughout the movie and is stationary
2. Click the spot (any frame) and make sure it has been found (magenta circle)
3. In the **Movie** menu, click **`Correct Drift`**
4. Check the result in the pop-up and save/load the movie if acceptable

### Kymographs<a name="kymographs"></a>

1. To generate a kymograph, enter **`Line`** mode using the switch under the movie (shortcut: **`N`**)
2. Optionally, enter maximum-projection mode to better see where you should draw lines
3. Draw the segmented line by placing green anchors on the movie
4. Double-click to complete the sequence and store a **kymograph**
5. If your movie has multiple channels, a kymograph will be generated for each, which will show when you toggle between channels (number keys)

### Track using kymograph reference<a name="kymoclick"></a>

1. To track a spot in the movie using the kymograph as a reference, draw a segmented line by placing blue anchors on the kymograph
2. Double-click to complete the sequence to generate a **trajectory**, where spot centers are searched in the movie from the linear interpolation between clicks
3. Press the **space bar** to play a movie of the trajectory
4. Assess the plots
    * The **spot histogram** shows the pixel intensities in the search range around the spot center and highlights the intensities within the spot
    * The **intensity plot** shows each spot's integrated intensity
    * The **speed histogram** shows the frame-to-frame speeds of the spots and overlay sthe net speed (only considering the start and end point)
5. If necessary, modify the search radius (hold down **`R`** and scroll) and re-attempt (**`Enter`** key)
6. If necessary, toggle tracking modes with the **`T`** key and re-attempt (**`Enter`** key)
    * **Independent**: each frame is treated independently using the interpolated search centers
    * **Tracked**: each frame's search center is based on the previous frame's spot center
    * **Smooth**: equivalent to *Independent* mode but goes through a filtering at the end to remove spots that are far off the main track
7. Click any point in the kymograph or in the *intensity plot* to jump to that point

### Modify individual points<a name="modify"></a>

* When a point is highlighted, use the **`X`** key to either invalidate the spot or re-attempt a fit if it is already invalid

### Browse trajectories<a name="browsetrajectories"></a>

* Adding trajectories will append the data to the **trajectory table**
* Right click a trajectory in the table to show some helpful options, like **Go to kymograph ch1-001**

## License<a name="license"></a>

This project is licensed under the MIT License - see the [LICENSE.txt](https://github.com/sami-chaaban/tracy/blob/main/LICENSE.txt) file for details.
