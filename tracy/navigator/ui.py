from ._shared import *


def _label_text_rect(widget, option):
    se_label = getattr(QtWidgets.QStyle, "SE_LabelContents", None)
    if se_label is not None:
        return widget.style().subElementRect(se_label, option, widget)
    return widget.contentsRect()


class ElidedLabel(QLabel):
    def __init__(self, text="", parent=None, elide_mode=Qt.ElideRight):
        super().__init__(text, parent)
        self._elide_mode = elide_mode
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setMinimumWidth(0)

    def minimumSizeHint(self):
        hint = super().minimumSizeHint()
        return QSize(0, hint.height())

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        option = QtWidgets.QStyleOption()
        option.initFrom(self)
        self.style().drawPrimitive(QtWidgets.QStyle.PE_Widget, option, painter, self)

        text_rect = _label_text_rect(self, option)
        elided = self.fontMetrics().elidedText(super().text(), self._elide_mode, text_rect.width())
        self.style().drawItemText(
            painter,
            text_rect,
            self.alignment(),
            option.palette,
            self.isEnabled(),
            elided,
        )


class ElidedClickableLabel(ClickableLabel):
    def __init__(self, text="", parent=None, elide_mode=Qt.ElideRight):
        super().__init__(text, parent)
        self._elide_mode = elide_mode
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setMinimumWidth(0)

    def minimumSizeHint(self):
        hint = super().minimumSizeHint()
        return QSize(0, hint.height())

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        option = QtWidgets.QStyleOption()
        option.initFrom(self)
        self.style().drawPrimitive(QtWidgets.QStyle.PE_Widget, option, painter, self)

        text_rect = _label_text_rect(self, option)
        elided = self.fontMetrics().elidedText(super().text(), self._elide_mode, text_rect.width())
        self.style().drawItemText(
            painter,
            text_rect,
            self.alignment(),
            option.palette,
            self.isEnabled(),
            elided,
        )

class NavigatorUiMixin:
    def create_ui(self):
        # Create the central widget and overall layout.
        central = QWidget()
        central.setObjectName("centralContainer")
        self.setCentralWidget(central)
        containerLayout = QVBoxLayout(central)

        # videoiconpath = self.resource_path('icons/video-camera.svg')
        crossiconpath = self.resource_path('icons/cross-small.svg')
        crossdoticonpath = self.resource_path('icons/cross-dot.svg')
        resetcontrastpath = self.resource_path('icons/contrast.svg')
        maxiconpath = self.resource_path('icons/max.svg')
        referenceiconpath = self.resource_path('icons/reference.svg')
        trajoverlayiconpath = self.resource_path('icons/overlay_traj.svg')
        trajoverlayoneiconpath = self.resource_path('icons/overlay_one.svg')
        roioverlayiconpath = self.resource_path('icons/overlay.svg')
        kymoanchoriconpath = self.resource_path('icons/overlay_anchor.svg')

        # --- Top Controls Section ---
        topWidget = QWidget()
        topLayout = QHBoxLayout(topWidget)
        topLayout.setSpacing(5)
        topLayout.setContentsMargins(20, 6, 0, 0)
        topLayout.setAlignment(Qt.AlignLeft)

        self.movieNameLabel = ElidedClickableLabel("LOAD")
        self.movieNameLabel.setObjectName("movieNameLabel")
        self.movieNameLabel.setProperty("pressed", False)
        self.movieNameLabel.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self.movieNameLabel.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.movieNameLabel.clicked.connect(self.handle_movie_load)
        self.movieNameLabel.setStyleSheet("""
        #movieNameLabel {
            background: transparent;
            color: black;
            font-size: 14px;
            border: 1px solid #DCE6FF;
            border-radius: 8px;
            padding: 6px 12px
        }
        #movieNameLabel:hover {
            background: rgb(215, 225, 252);
        }
        #movieNameLabel[pressed="true"] {
            background: #DCE6FF;
        }
        """)
        topLayout.addWidget(self.movieNameLabel)
        load_tip_filter = BubbleTipFilter("Load single- or multi-channel TIFF", self, placement="right")
        self.movieNameLabel.installEventFilter(load_tip_filter)
        self.movieNameLabel._bubble_filter = load_tip_filter
        self._load_tip_filter = load_tip_filter
        QTimer.singleShot(8000, self._maybe_show_load_tip)

        
        # Search window radius control.
        self.searchWindowSpin = QSpinBox()
        self.searchWindowSpin.setRange(4, 50)
        self.searchWindowSpin.setValue(12)
        self.searchWindowSpin.setFocusPolicy(Qt.NoFocus)
        # Set a fixed size so it's square
        self.searchWindowSpin.setFixedSize(60, 35)
        #topLayout.addWidget(create_pair("Search radius:", self.searchWindowSpin))
        
        self.insetViewSize = QSpinBox()
        self.insetViewSize.setRange(4, 14)
        self.insetViewSize.setValue(12)
        
        # Add stretch and right-aligned labels.
        topLayout.addStretch()
        self.pixelValueLabel = ElidedLabel("")
        self.pixelValueLabel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.pixelValueLabel.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.pixelValueLabel.setStyleSheet("color: #444444; background: transparent; padding-right: 25px;")
        topLayout.addWidget(self.pixelValueLabel)

        self.scaleLabel = ElidedClickableLabel("")
        self.scaleLabel.setObjectName("scaleLabel")
        self.scaleLabel.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self.scaleLabel.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.scaleLabel.setStyleSheet("""
        #scaleLabel {
            color: black;
            background: transparent;
            font-size: 14px;
            border-radius: 8px;
            padding: 6px 6px 6px 6px
        }
        #scaleLabel:hover {
            background: rgb(215, 225, 252);
        }
        #scaleLabel[pressed="true"] {
            background: #DCE6FF;
        }
        """)
        self.scaleLabel.clicked.connect(self.open_set_scale_dialog)
        topLayout.addWidget(self.scaleLabel)
        containerLayout.addWidget(topWidget)

        topLayout.addSpacing(25)
        
        # --- Main Horizontal Splitter ---
        self.mainSplitter = CustomSplitter(Qt.Horizontal, handle_y_offset_pct=0.4955)
        containerLayout.addWidget(self.mainSplitter, stretch=1)
        
        # LEFT COLUMN: Kymograph and ROI controls.
        self.leftWidget = QWidget()
        leftLayout = QVBoxLayout(self.leftWidget)
        # Instead, create a horizontal layout that puts the label to the left of the dropdown
        kymoControlLayout = QHBoxLayout()


        # Create the kymograph label, right justified
        kymoLabel = QLabel("KYMO")
        kymoLabel.setStyleSheet("font-size: 11px;")
        # kymoLabel.setStyleSheet("color: #666666;")
        kymoLabel.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        # Optional: set a fixed minimum width for label alignment.
        kymoLabel.setMinimumWidth(40)
        kymoControlLayout.addWidget(kymoLabel)

        # Add the kymograph combo box
        self.kymoCombo = QComboBox()
        self.kymoCombo.setEnabled(False)
        self.kymoCombo.setView(QListView())
        self.kymoCombo.currentIndexChanged.connect(self.kymo_changed)
        kymoControlLayout.addWidget(self.kymoCombo)

        # Add the kymograph delete and clear buttons
        self.kymoDeleteBtn = QPushButton("")
        self.kymoDeleteBtn.setIcon(QIcon(crossiconpath))
        self.kymoDeleteBtn.setIconSize(QSize(14, 14))
        # self.kymoDeleteBtn.setToolTip("Delete selected ROI")
        deletekymo_filter = BubbleTipFilter("Delete this kymograph", self)
        self.kymoDeleteBtn.installEventFilter(deletekymo_filter)
        self.kymoDeleteBtn._bubble_filter = deletekymo_filter
        self.kymoDeleteBtn.setObjectName("Passive")
        self.kymoDeleteBtn.setFixedWidth(32)
        self.kymoDeleteBtn.clicked.connect(self.delete_current_kymograph)
        kymoControlLayout.addWidget(self.kymoDeleteBtn)

        self.clearKymoBtn = QPushButton("")
        self.clearKymoBtn.setIcon(QIcon(crossdoticonpath))
        self.clearKymoBtn.setIconSize(QSize(14, 14))
        # self.clearKymoBtn.setToolTip("Clear kymographs")
        clearkymo_filter = BubbleTipFilter("Delete all kymographs", self)
        self.clearKymoBtn.installEventFilter(clearkymo_filter)
        self.clearKymoBtn._bubble_filter = clearkymo_filter
        self.clearKymoBtn.setObjectName("Passive")
        self.clearKymoBtn.setFixedWidth(32)
        self.clearKymoBtn.clicked.connect(
            lambda _checked: self.clear_kymographs()
        )
        kymoControlLayout.addWidget(self.clearKymoBtn)


        # Wrap the horizontal layout in a container widget.
        kymoContainer = QWidget()
        kymoContainer.setLayout(kymoControlLayout)

        # Now add the container widget to the vertical layout with the desired alignment.
        leftLayout.addWidget(kymoContainer, alignment=Qt.AlignCenter)
        
        roiControlLayout = QHBoxLayout()

        # Create the ROI label, right justified
        roiLabel = QLabel("ROI")
        roiLabel.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        roiLabel.setMinimumWidth(40)
        roiControlLayout.addWidget(roiLabel)

        # Add the ROI combo box
        self.roiCombo = QComboBox()
        self.roiCombo.setEnabled(False)
        self.roiCombo.setView(QListView())
        self.roiCombo.currentIndexChanged.connect(self.update_roi_overlay_if_active)
        roiControlLayout.addWidget(self.roiCombo)

        # Add the ROI delete and clear buttons
        self.roiDeleteBtn = AnimatedIconButton("")
        self.roiDeleteBtn.setIcon(QIcon(crossiconpath))
        self.roiDeleteBtn.setIconSize(QSize(14, 14))
        # self.roiDeleteBtn.setToolTip("Delete selected ROI")
        roidelete_filter = BubbleTipFilter("Delete selected line ROI", self)
        self.roiDeleteBtn.installEventFilter(roidelete_filter)
        self.roiDeleteBtn._bubble_filter = roidelete_filter
        self.roiDeleteBtn.setObjectName("Passive")
        self.roiDeleteBtn.setFixedWidth(32)
        self.roiDeleteBtn.clicked.connect(self.delete_current_roi)
        roiControlLayout.addWidget(self.roiDeleteBtn)

        self.clearROIBtn = AnimatedIconButton("")
        self.clearROIBtn.setIcon(QIcon(crossdoticonpath))
        self.clearROIBtn.setIconSize(QSize(14, 14))
        # self.clearROIBtn.setToolTip("Clear ROIs")
        clearroi_filter = BubbleTipFilter("Delete all line ROIs", self)
        self.clearROIBtn.installEventFilter(clearroi_filter)
        self.clearROIBtn._bubble_filter = clearroi_filter
        self.clearROIBtn.setObjectName("Passive")
        self.clearROIBtn.setFixedWidth(32)
        self.clearROIBtn.clicked.connect(self.clear_rois)
        roiControlLayout.addWidget(self.clearROIBtn)

        # Wrap the horizontal layout in a container widget.
        self.roiContainer = QWidget()
        self.roiContainer.setLayout(roiControlLayout)

        # Now add the container widget to the vertical layout with the desired alignment.
        leftLayout.addWidget(self.roiContainer, alignment=Qt.AlignCenter)

        kymoControlLayout.setSpacing(6)
        roiControlLayout.setSpacing(6)
        kymoContainer.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.roiContainer.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        kymoContainer.setContentsMargins(0, 0, 0, 0)
        self.roiContainer.setContentsMargins(0, 0, 0, 0)
        kymoContainer.setFixedHeight(50)
        self.roiContainer.setFixedHeight(50)
        leftLayout.setContentsMargins(6, 0, 6, 1)
        leftLayout.setSpacing(0)
        self.roiContainer.setVisible(False)

        # Create a RoundedFrame container with configured radius/background.
        roundedContainer = RoundedFrame(parent=self, radius=10, bg_color = self.settings['widget-bg'])
        shadow_effect = QGraphicsDropShadowEffect(roundedContainer)
        shadow_effect.setBlurRadius(10)                # Adjust for a softer or sharper shadow.
        shadow_effect.setColor(QColor(0, 0, 0, 120))     # Semi-transparent black (adjust alpha as needed).
        shadow_effect.setOffset(0, 0)                    # Zero offset for a symmetric shadow.
        # Apply the shadow effect to the container.
        roundedContainer.setGraphicsEffect(shadow_effect)
        # Create the kymograph canvas.
        self.kymoCanvas = KymoCanvas(self, navigator=self)
        self.kymoCanvas.setFocusPolicy(Qt.StrongFocus)
        self.kymoCanvas.setFocus()
        # Connect kymograph mouse events.
        self.kymoCanvas.mpl_connect("button_press_event", self.on_kymo_click)
        self.kymoCanvas.mpl_connect("motion_notify_event", self.on_kymo_motion)
        self.kymoCanvas.mpl_connect("button_release_event", self.on_kymo_release)
        self.kymoCanvas.mpl_connect("motion_notify_event", self.on_kymo_hover)
        self.kymoCanvas.mpl_connect("axes_leave_event", self.on_kymo_leave)
        self.kymoCanvas.mpl_connect("figure_leave_event", self.on_kymo_leave)
        self.kymoCanvas.mpl_connect("pick_event", self._on_kymo_label_pick)

        self.kymoCanvas.setContextMenuPolicy(Qt.CustomContextMenu)
        self.kymoCanvas.customContextMenuRequested.connect(self._show_kymo_context_menu)

        # Create a layout for the RoundedFrame and add kymoCanvas to it.
        roundedLayout = QVBoxLayout(roundedContainer)
        roundedContainer.setStyleSheet(f"background-color: {self.settings['widget-bg']}")
        roundedLayout.setContentsMargins(5, 5, 5, 5)  # Optional: adjust the inner margin for spacing
        roundedLayout.addWidget(self.kymoCanvas)

        # Add the rounded container to leftLayout.
        leftLayout.addWidget(roundedContainer, stretch=1)
        
        # 1) make a legend container as a child of the rounded frame
        self.kymoLegendWidget = QWidget(parent=roundedContainer)
        self.kymoLegendWidget.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Minimum)
        self.kymoLegendWidget.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.kymoLegendWidget.hide()  # start hidden

        # 2) style / position it absolutely
        self.kymoLegendWidget.setStyleSheet("background: white;  border-radius: 8px; ")
        self.kymoLegendWidget.move(10, 10)  # 10px from top-left of roundedContainer
        kymolegendshadow = QGraphicsDropShadowEffect(self.kymoLegendWidget)
        kymolegendshadow.setBlurRadius(10)
        kymolegendshadow.setColor(QColor(0, 0, 0, 120))
        kymolegendshadow.setOffset(0, 0)
        self.kymoLegendWidget.setGraphicsEffect(kymolegendshadow)
        self.kymoLegendLayout = QHBoxLayout(self.kymoLegendWidget)
        self.kymoLegendLayout.setSizeConstraint(QLayout.SetFixedSize)
        self.kymoLegendLayout.setContentsMargins(5,5,5,5)
        self.kymoLegendLayout.setSpacing(5)
        self.kymoLegendWidget.installEventFilter(self)

        kymocontrastwidget = QWidget()
        kymocontrastLayout = QHBoxLayout(kymocontrastwidget)
        kymocontrastLayout.setContentsMargins(0, 0, 0, 0)
        kymocontrastLayout.setSpacing(4)
        self.kymocontrastControlsWidget = KymoContrastControlsWidget(self.kymoCanvas)
        kymocontrastsliderfilter = BubbleTipFilter("Adjust contrast range", self, placement="right")
        self.kymocontrastControlsWidget.installEventFilter(kymocontrastsliderfilter)
        self.kymocontrastControlsWidget._bubble_filter = kymocontrastsliderfilter
        self.kymocontrastControlsWidget.setMinimumWidth(100)
        kymo_contrast_container = QWidget()
        kymo_contrast_layout = QVBoxLayout(kymo_contrast_container)
        kymo_contrast_layout.setContentsMargins(0, 0, 0, 0)
        kymo_contrast_layout.setSpacing(0)
        kymo_contrast_layout.setAlignment(Qt.AlignHCenter)
        kymo_contrast_label = QLabel("CONTRAST")
        kymo_contrast_label.setStyleSheet("color: black; font-size: 9px;")
        kymo_contrast_label.adjustSize()
        kymo_label_spacer = max(2, kymo_contrast_label.sizeHint().height() // 2)
        kymo_contrast_layout.addSpacing(kymo_label_spacer)
        kymo_contrast_layout.addWidget(self.kymocontrastControlsWidget, alignment=Qt.AlignHCenter)
        kymo_contrast_layout.addWidget(kymo_contrast_label, alignment=Qt.AlignHCenter)
        kymocontrastLayout.addWidget(kymo_contrast_container)
        kymocontrastLayout.setAlignment(kymo_contrast_container, Qt.AlignBottom)
        self.kymoresetBtn = AnimatedIconButton("")
        self.kymoresetBtn.setIcon(QIcon(resetcontrastpath))
        self.kymoresetBtn.setIconSize(QSize(16, 16))
        kymocontrastresetfilter = BubbleTipFilter("Reset contrast", self, placement="left")
        self.kymoresetBtn.installEventFilter(kymocontrastresetfilter)
        self.kymoresetBtn._bubble_filter = kymocontrastresetfilter
        self.kymoresetBtn.clicked.connect(self.reset_kymo_contrast)
        self.kymoresetBtn.setObjectName("Passive")
        self.kymoresetBtn.setFixedSize(36, 36)
        kymo_reset_container = QWidget()
        kymo_reset_layout = QVBoxLayout(kymo_reset_container)
        kymo_reset_layout.setContentsMargins(0, 0, 0, 0)
        kymo_reset_layout.setSpacing(0)
        kymo_reset_layout.setAlignment(Qt.AlignHCenter)
        kymo_reset_label = QLabel("AUTO")
        kymo_reset_label.setStyleSheet("color: black; font-size: 9px;")
        kymo_reset_label.adjustSize()
        kymo_reset_layout.addSpacing(kymo_label_spacer)
        kymo_reset_layout.addWidget(self.kymoresetBtn, alignment=Qt.AlignHCenter)
        kymo_reset_layout.addWidget(kymo_reset_label, alignment=Qt.AlignHCenter)
        kymocontrastLayout.addWidget(kymo_reset_container)
        kymocontrastLayout.setAlignment(kymo_reset_container, Qt.AlignBottom)
        kymocontrastLayout.addSpacing(6)

        self.kymo_anchor_overlay_button = AnimatedIconButton("")
        kymoanchorfilter = BubbleTipFilter("Show anchors (hold shift key to edit them)", self, placement="right")
        self.kymo_anchor_overlay_button.installEventFilter(kymoanchorfilter)
        self.kymo_anchor_overlay_button._bubble_filter = kymoanchorfilter
        self.kymo_anchor_overlay_button.setIcon(QIcon(kymoanchoriconpath))
        self.kymo_anchor_overlay_button.setIconSize(QSize(16, 16))
        self.kymo_anchor_overlay_button.setFixedSize(36, 36)
        self.kymo_anchor_overlay_button.setCheckable(True)
        self.kymo_anchor_overlay_button.setChecked(True)
        self.kymo_anchor_overlay_button.setObjectName("Toggle")
        self.kymo_anchor_overlay_button.clicked.connect(self.toggle_kymo_anchor_overlay)
        anchor_container = QWidget()
        anchor_layout = QVBoxLayout(anchor_container)
        anchor_layout.setContentsMargins(0, 0, 0, 0)
        anchor_layout.setSpacing(0)
        anchor_layout.setAlignment(Qt.AlignHCenter)
        anchor_label = QLabel("ANCHORS")
        anchor_label.setStyleSheet("color: black; font-size: 9px;")
        anchor_label.adjustSize()
        anchor_layout.addSpacing(kymo_label_spacer)
        anchor_layout.addWidget(self.kymo_anchor_overlay_button, alignment=Qt.AlignHCenter)
        anchor_layout.addWidget(anchor_label, alignment=Qt.AlignHCenter)
        kymocontrastLayout.addSpacing(18)
        kymocontrastLayout.addWidget(anchor_container)
        kymocontrastLayout.setAlignment(anchor_container, Qt.AlignBottom)

        leftLayout.addSpacing(2)
        leftLayout.addWidget(kymocontrastwidget, alignment=Qt.AlignCenter)

        self.leftWidget.setLayout(leftLayout)
        self.mainSplitter.addWidget(self.leftWidget)

        # RIGHT SPLITTER: Two columns — the movie widget and the right panel.
        self.rightVerticalSplitter = CustomSplitter(Qt.Vertical)
        self.topRightSplitter = CustomSplitter(Qt.Horizontal)
        
        # 2nd Column: Movie widget
        self.movieWidget = QWidget()
        movieLayout = QVBoxLayout(self.movieWidget)
        movieLayout.setContentsMargins(6, 6, 6, 6)
        movieLayout.setSpacing(0)
        self.movieDisplayContainer = RoundedFrame(self, radius=10, bg_color = self.settings['widget-bg'])
        self.movieDisplayContainer.setStyleSheet(
            "QFrame { "
            "  border: 0px solid transparent; "
            "  border-radius: 10px; "
            "}"
        )
        shadow = QGraphicsDropShadowEffect(self.movieDisplayContainer)
        shadow.setBlurRadius(10)
        shadow.setColor(QColor(0, 0, 0, 120))
        shadow.setOffset(0)
        self.movieDisplayContainer.setGraphicsEffect(shadow)
        self.movieDisplayContainer.setMinimumSize(100, 100)
        self.movieDisplayContainer.setFrameStyle(QFrame.Box)
        self.movieDisplayContainer.setLineWidth(2)
        self.movieDisplayContainer.setStyleSheet("QFrame { border: 6px solid transparent; }")
        movieDisplayLayout = QVBoxLayout(self.movieDisplayContainer)
        movieDisplayLayout.setContentsMargins(6, 5, 6, 5)

        self.movieChannelCombo = QComboBox()
        self.movieChannelCombo.setView(QListView())
        self.movieChannelCombo.setFixedWidth(40)
        self.movieChannelCombo.setFixedHeight(24)
        self.movieChannelCombo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.movieChannelCombo.setStyleSheet("QComboBox { min-width: 40px; background: transparent; margin: 0px; border: 1px solid #ccc;  color: #444444 }")
        self.movieChannelCombo.setEnabled(False)
        self.movieChannelCombo.currentIndexChanged.connect(self.on_channel_changed)


        # Initially hide the container until a movie is loaded
        self.channelControlContainer = RoundedFrame(self.movieDisplayContainer, radius=10, bg_color = self.settings['widget-bg'])
        self.channelControlContainer.setParent(self.movieDisplayContainer)
        self.channelControlContainer.setStyleSheet("background: transparent;")
        self.channelControlContainer.move(10, 10)   # tweak x/y offsets
        self.channelControlContainer.raise_()
        self.channelControlContainer.setVisible(False)

        self.movieCanvas = MovieCanvas(self, navigator=self)
        self.movieCanvas.setStyleSheet(f"background-color: {self.settings['widget-bg']}")
        self.movieCanvas.mpl_connect("scroll_event", self.movieCanvas.on_scroll)
        # self.movieCanvas.mpl_connect("scroll_event", self.on_movie_scroll)
        self.movieCanvas.mpl_connect("button_press_event", self.on_movie_click)
        self.movieCanvas.mpl_connect("button_release_event", self.on_movie_release)
        self.movieCanvas.mpl_connect("motion_notify_event", self.on_movie_motion)
        movieDisplayLayout.addWidget(self.movieCanvas, stretch=1)
        self.movieDisplayContainer.setLayout(movieDisplayLayout)
        movieLayout.addWidget(self.movieDisplayContainer, stretch=1)

        # in create_ui, replace the overlay QLabel with:
        self._ch_overlay = ClickableLabel("", parent=self.movieDisplayContainer)
        channellabelfilter = BubbleTipFilter("Change channel (shortcut: 1, 2...)", self)
        self._ch_overlay.installEventFilter(channellabelfilter)
        self._ch_overlay._bubble_filter = channellabelfilter
        self._ch_overlay.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self._ch_overlay.setStyleSheet("""
            background: white;
            border-radius: 12px;
            color: black;
            font-size: 26px;
            font-weight: bold;
        """)
        chshadow = QGraphicsDropShadowEffect(self._ch_overlay)
        chshadow.setBlurRadius(10)
        chshadow.setColor(QColor(0, 0, 0, 120))
        chshadow.setOffset(0, 0)
        self._ch_overlay.setGraphicsEffect(chshadow)
        self._ch_overlay.setMinimumSize(0, 0)
        self._ch_overlay.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self._ch_overlay.hide()
        self._ch_overlay.clicked.connect(self._on_overlay_clicked)

        # 1) make a legend container as a child of the rounded frame
        self.movieLegendWidget = QWidget(parent=self.movieDisplayContainer)
        self.movieLegendWidget.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Minimum)
        self.movieLegendWidget.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.movieLegendWidget.hide()  # start hidden

        # 2) style / position it absolutely
        self.movieLegendWidget.setStyleSheet("background: white;  border-radius: 8px; ")
        self.movieLegendWidget.move(0, 0)  # 10px from top-left of roundedContainer
        movielegendshadow = QGraphicsDropShadowEffect(self.movieLegendWidget)
        movielegendshadow.setBlurRadius(10)
        movielegendshadow.setColor(QColor(0, 0, 0, 120))
        movielegendshadow.setOffset(0, 0)
        self.movieLegendWidget.setGraphicsEffect(movielegendshadow)
        # 3) give it a simple horizontal layout
        self.movieLegendLayout = QHBoxLayout(self.movieLegendWidget)
        self.movieLegendLayout.setSizeConstraint(QLayout.SetFixedSize)
        self.movieLegendLayout.setContentsMargins(5,0,5,0)
        self.movieLegendLayout.setSpacing(8)
        self.movieLegendWidget.installEventFilter(self)

        self.movieLegendWidget.stackUnder(self._ch_overlay)
        self._ch_overlay.installEventFilter(self)
        self.movieDisplayContainer.installEventFilter(self)
        QTimer.singleShot(0, self.movieCanvas.start_idle_animation)

        movieLayout.addSpacing(4)
        
        sliderWidget = QWidget()
        sliderLayout = QHBoxLayout(sliderWidget)
        sliderLayout.setContentsMargins(6, 3, 6, 0)
        sliderLayout.setSpacing(10)
        self.frameNumberLabel = QLabel("1")
        self.frameNumberLabel.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        sliderLayout.addWidget(self.frameNumberLabel)
        self.frameSlider = QSlider(Qt.Horizontal)
        self.frameSlider.setMinimum(0)
        self.frameSlider.setMaximum(250)  # will be updated after loading movie
        self.frameSlider.setValue(0)
        self.frameSlider.valueChanged.connect(self.on_frame_slider_changed)
        self.frameSlider.setSingleStep(1)
        self.frameSlider.setPageStep(1)
        font_metrics = self.frameNumberLabel.fontMetrics()
        width = font_metrics.width("99999")
        self.frameNumberLabel.setFixedWidth(width)
        sliderLayout.addWidget(self.frameSlider)
        movieLayout.addWidget(sliderWidget)
        movieLayout.addSpacing(0)
        
        contrastWidget = QWidget()
        contrastLayout = QHBoxLayout(contrastWidget)
        contrastLayout.setContentsMargins(0, 0, 0, 0)
        contrastLayout.setSpacing(4)
        self.contrastControlsWidget = ContrastControlsWidget(self.movieCanvas)
        contrastsliderfilter = BubbleTipFilter("Adjust contrast range", self, placement="left")
        self.contrastControlsWidget.installEventFilter(contrastsliderfilter)
        self.contrastControlsWidget._bubble_filter = contrastsliderfilter
        self.contrastControlsWidget.setMinimumWidth(100)
        contrast_container = QWidget()
        contrast_label_layout = QVBoxLayout(contrast_container)
        contrast_label_layout.setContentsMargins(0, 0, 0, 0)
        contrast_label_layout.setSpacing(0)
        contrast_label_layout.setAlignment(Qt.AlignHCenter)
        contrast_label = QLabel("CONTRAST")
        contrast_label.setStyleSheet("color: black; font-size: 9px;")
        contrast_label.adjustSize()
        movie_label_spacer = max(2, contrast_label.sizeHint().height() // 2)
        contrast_label_layout.addSpacing(movie_label_spacer)
        contrast_label_layout.addWidget(self.contrastControlsWidget, alignment=Qt.AlignHCenter)
        contrast_label_layout.addWidget(contrast_label, alignment=Qt.AlignHCenter)
        contrastLayout.addWidget(contrast_container)
        contrastLayout.setAlignment(contrast_container, Qt.AlignBottom)
        self.resetBtn = AnimatedIconButton("")
        self.resetBtn.setIcon(QIcon(resetcontrastpath))
        self.resetBtn.setIconSize(QSize(16, 16))
        # self.resetBtn.setToolTip("Reset contrast")
        contrastresetfilter = BubbleTipFilter("Reset contrast", self, placement="left")
        self.resetBtn.installEventFilter(contrastresetfilter)
        self.resetBtn._bubble_filter = contrastresetfilter
        self.resetBtn.clicked.connect(self.reset_contrast)
        self.resetBtn.setObjectName("Passive")
        self.resetBtn.setFixedSize(36, 36)
        reset_container = QWidget()
        reset_layout = QVBoxLayout(reset_container)
        reset_layout.setContentsMargins(0, 0, 0, 0)
        reset_layout.setSpacing(0)
        reset_layout.setAlignment(Qt.AlignHCenter)
        reset_label = QLabel("AUTO")
        reset_label.setStyleSheet("color: black; font-size: 9px;")
        reset_label.adjustSize()
        reset_layout.addSpacing(movie_label_spacer)
        reset_layout.addWidget(self.resetBtn, alignment=Qt.AlignHCenter)
        reset_layout.addWidget(reset_label, alignment=Qt.AlignHCenter)
        contrastLayout.addWidget(reset_container)
        contrastLayout.setAlignment(reset_container, Qt.AlignBottom)
        contrastLayout.addSpacing(16)
        self.sumBtn = AnimatedIconButton("", self)
        self.sumBtn.setIcon(QIcon(maxiconpath))
        self.sumBtn.setIconSize(QSize(16, 16))
        self.sumBtn.setCheckable(True)
        self.sumBtn.setFixedSize(36, 36)
        # self.sumBtn.setToolTip("Show the maximum projection (shortcut: m)")
        sumfilter = BubbleTipFilter("Maximum projection (shortcut: m)", self)
        self.sumBtn.installEventFilter(sumfilter)
        self.sumBtn._bubble_filter = sumfilter
        self.sumBtn.toggled.connect(self.on_sum_toggled)
        self.sumBtn.setObjectName("Toggle")

        sum_container = QWidget()
        sum_layout = QVBoxLayout(sum_container)
        sum_layout.setContentsMargins(0, 0, 0, 0)
        sum_layout.setSpacing(0)
        sum_layout.setAlignment(Qt.AlignHCenter)
        sum_label = QLabel("MAX")
        sum_label.setStyleSheet("color: black; font-size: 9px;")
        sum_label.adjustSize()
        sum_layout.addSpacing(movie_label_spacer)
        sum_layout.addWidget(self.sumBtn, alignment=Qt.AlignHCenter)
        sum_layout.addWidget(sum_label, alignment=Qt.AlignHCenter)
        contrastLayout.addWidget(sum_container)
        contrastLayout.setAlignment(sum_container, Qt.AlignBottom)
        contrastLayout.addSpacing(12)

        self.refBtn = AnimatedIconButton("")
        self.refBtn.setIcon(QIcon(referenceiconpath))
        self.refBtn.setIconSize(QSize(16, 16))
        # self.refBtn.setToolTip("Show the reference image")
        reffilter = BubbleTipFilter("Reference image (use ctrl/cmd+arrows to nudge)", self)
        self.refBtn.installEventFilter(reffilter)
        self.refBtn._bubble_filter = reffilter
        self.refBtn.setCheckable(True)
        self.refBtn.setFixedSize(36, 36)
        self.refBtn.setVisible(False)
        self.refBtn.toggled.connect(self.on_ref_toggled)
        self.refBtn.setObjectName("Toggle")
        ref_container = QWidget()
        ref_layout = QVBoxLayout(ref_container)
        ref_layout.setContentsMargins(0, 0, 0, 0)
        ref_layout.setSpacing(0)
        ref_layout.setAlignment(Qt.AlignHCenter)
        ref_label = QLabel("REF")
        ref_label.setStyleSheet("color: black; font-size: 9px;")
        ref_label.adjustSize()
        ref_spacer_height = ref_label.sizeHint().height() + 1
        ref_layout.addSpacing(ref_spacer_height)
        ref_layout.addWidget(self.refBtn, alignment=Qt.AlignHCenter)
        ref_layout.addWidget(ref_label, alignment=Qt.AlignHCenter)
        contrastLayout.addWidget(ref_container)
        contrastLayout.setAlignment(ref_container, Qt.AlignBottom)
        ref_container.setVisible(False)
        self.ref_container = ref_container
        ref_spacer = QWidget()
        ref_spacer.setFixedWidth(12)
        ref_spacer.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Minimum)
        contrastLayout.addWidget(ref_spacer)
        contrastLayout.setAlignment(ref_spacer, Qt.AlignBottom)
        ref_spacer.setVisible(False)
        self.ref_spacer = ref_spacer

        self.traj_overlay_button = AnimatedIconButton("")
        # self.traj_overlay_button.setToolTip("Overlay trajectories (shortcut: o)")
        traj_filter = BubbleTipFilter(
            "Overlay trajectories (all > one > none; shortcut: o)",
            self
        )
        self.traj_overlay_button.installEventFilter(traj_filter)
        self.traj_overlay_button._bubble_filter = traj_filter
        self._traj_overlay_icons = {
            "all": QIcon(trajoverlayiconpath),
            "selected": QIcon(trajoverlayoneiconpath),
        }
        self.traj_overlay_button.setIcon(self._traj_overlay_icons["all"])
        self.traj_overlay_button.setIconSize(QSize(16, 16))
        self.traj_overlay_button.setFixedSize(36, 36)
        self.traj_overlay_button.setCheckable(True)
        self.traj_overlay_mode = "all"
        self._apply_traj_overlay_mode(self.traj_overlay_mode, redraw=False)
        self.traj_overlay_button.setObjectName("Toggle")
        # self.update_overlay_button_style(self.traj_overlay_button.isChecked())
        # self.traj_overlay_button.toggled.connect(self.update_overlay_button_style)
        traj_container = QWidget()
        traj_layout = QVBoxLayout(traj_container)
        traj_layout.setContentsMargins(0, 0, 0, 0)
        traj_layout.setSpacing(0)
        traj_layout.setAlignment(Qt.AlignHCenter)
        traj_label = QLabel("SPOTS")
        traj_label.setStyleSheet("color: black; font-size: 9px;")
        traj_label.adjustSize()
        traj_layout.addSpacing(movie_label_spacer)
        traj_layout.addWidget(self.traj_overlay_button, alignment=Qt.AlignHCenter)
        traj_layout.addWidget(traj_label, alignment=Qt.AlignHCenter)
        contrastLayout.addWidget(traj_container)
        self.traj_overlay_container = traj_container
        contrastLayout.setAlignment(traj_container, Qt.AlignBottom)
        contrastLayout.addSpacing(18)

        self.modeSwitch = ToggleSwitch()
        self.modeSwitch.toggled.connect(lambda state: self.onModeChanged("roi" if state else "spot"))
        mode_container = QWidget()
        mode_layout = QVBoxLayout(mode_container)
        mode_layout.setContentsMargins(0, 0, 0, 0)
        mode_layout.setSpacing(0)
        mode_layout.setAlignment(Qt.AlignHCenter)
        mode_label = QLabel("MODE")
        mode_label.setStyleSheet("color: black; font-size: 9px;")
        mode_label.adjustSize()
        mode_layout.addSpacing(movie_label_spacer)
        mode_layout.addWidget(self.modeSwitch, alignment=Qt.AlignHCenter)
        mode_layout.addWidget(mode_label, alignment=Qt.AlignHCenter)
        contrastLayout.addWidget(mode_container)
        contrastLayout.setAlignment(mode_container, Qt.AlignBottom)
        contrastLayout.addSpacing(24)
        switch_filter = BubbleTipFilter("Switch between finding spots and drawing kymographs (shortcut: n)", self)
        self.modeSwitch.installEventFilter(switch_filter)
        # keep a ref so Python doesn’t garbage‐collect it
        self.modeSwitch._bubble_filter = switch_filter

        self.roi_overlay_button = AnimatedIconButton("")
        self.roi_overlay_button.setIcon(QIcon(roioverlayiconpath))
        self.roi_overlay_button.setIconSize(QSize(16, 16))
        # self.roi_overlay_button.setToolTip("Overlay ROI onto the movie")
        overlayroi_filter = BubbleTipFilter("Overlay kymograph lines (shortcut n)", self)
        self.roi_overlay_button.installEventFilter(overlayroi_filter)
        self.roi_overlay_button._bubble_filter = overlayroi_filter
        self.roi_overlay_button.setCheckable(True)
        self.roi_overlay_button.setFixedSize(36, 36)
        self.roi_overlay_button.setStyleSheet("""
            QPushButton {
                background-color: transparent;
            }
            QPushButton:checked, QPushButton:checked:hover {
                background-color: #81C784;
            }
            QPushButton:hover {
                background-color: #D8EDD9;
            }
        """)
        self.roi_overlay_button.clicked.connect(self.toggle_roi_overlay)
        # self.roi_overlay_button.toggled.connect(self.update_roi_overlay_button_style)
        roi_overlay_container = QWidget()
        roi_overlay_layout = QVBoxLayout(roi_overlay_container)
        roi_overlay_layout.setContentsMargins(0, 0, 0, 0)
        roi_overlay_layout.setSpacing(0)
        roi_overlay_layout.setAlignment(Qt.AlignHCenter)
        roi_label = QLabel("LINES")
        roi_label.setStyleSheet("color: black; font-size: 9px;")
        roi_label.adjustSize()
        roi_overlay_layout.addSpacing(movie_label_spacer)
        roi_overlay_layout.addWidget(self.roi_overlay_button, alignment=Qt.AlignHCenter)
        roi_overlay_layout.addWidget(roi_label, alignment=Qt.AlignHCenter)
        contrastLayout.addWidget(roi_overlay_container)
        contrastLayout.setAlignment(roi_overlay_container, Qt.AlignBottom)
        contrastLayout.addSpacing(24)

        self.delete_button = AnimatedIconButton("")
        # self.delete_button.setToolTip("Delete selected trajectory")
        deletetraj_filter = BubbleTipFilter("Delete selected trajectory", self)
        self.delete_button.installEventFilter(deletetraj_filter)
        self.delete_button._bubble_filter = deletetraj_filter
        self.delete_button.setIcon(QIcon(crossiconpath))
        self.delete_button.setIconSize(QSize(16, 16))
        self.delete_button.setFixedSize(36, 36)
        self.delete_button.setObjectName("Passive")
        delete_container = QWidget()
        delete_layout = QVBoxLayout(delete_container)
        delete_layout.setContentsMargins(0, 0, 0, 0)
        delete_layout.setSpacing(0)
        delete_layout.setAlignment(Qt.AlignHCenter)
        delete_label = QLabel("DEL.")
        delete_label.setStyleSheet("color: black; font-size: 9px;")
        delete_label.adjustSize()
        delete_layout.addSpacing(movie_label_spacer)
        delete_layout.addWidget(self.delete_button, alignment=Qt.AlignHCenter)
        delete_layout.addWidget(delete_label, alignment=Qt.AlignHCenter)
        contrastLayout.addWidget(delete_container)
        contrastLayout.setAlignment(delete_container, Qt.AlignBottom)
        self.delete_container = delete_container

        self.clear_button = AnimatedIconButton("")
        # self.clear_button.setToolTip("Delete all trajectories")
        deletealltraj_filter = BubbleTipFilter("Delete all trajectories", self)
        self.clear_button.installEventFilter(deletealltraj_filter)
        self.clear_button._bubble_filter = deletealltraj_filter
        self.clear_button.setIcon(QIcon(crossdoticonpath))
        self.clear_button.setIconSize(QSize(16, 16))
        self.clear_button.setFixedSize(36, 36)
        self.clear_button.setObjectName("Passive")
        clear_container = QWidget()
        clear_layout = QVBoxLayout(clear_container)
        clear_layout.setContentsMargins(0, 0, 0, 0)
        clear_layout.setSpacing(0)
        clear_layout.setAlignment(Qt.AlignHCenter)
        clear_label = QLabel("ALL")
        clear_label.setStyleSheet("color: black; font-size: 9px;")
        clear_label.adjustSize()
        clear_layout.addSpacing(movie_label_spacer)
        clear_layout.addWidget(self.clear_button, alignment=Qt.AlignHCenter)
        clear_layout.addWidget(clear_label, alignment=Qt.AlignHCenter)
        contrastLayout.addWidget(clear_container)
        contrastLayout.setAlignment(clear_container, Qt.AlignBottom)
        self.clear_container = clear_container

        movieLayout.addWidget(contrastWidget, alignment=Qt.AlignCenter)
        self.movieWidget.setLayout(movieLayout)
        self.topRightSplitter.addWidget(self.movieWidget)

        # Column 3: Right Panel with additional canvases.
        self._right_panel_width = 500
        self.rightPanel = QWidget()
        self.rightPanel.setFixedWidth(self._right_panel_width)
        rightPanelLayout = QVBoxLayout(self.rightPanel)
        rightPanelLayout.setContentsMargins(6, 6, 6, 6)
        rightPanelLayout.setSpacing(10)

        # --- Histogram Canvas in a rounded frame ---
        self.histogramCanvas = HistogramCanvas(self)
        self.histogramCanvas.setStyleSheet(f"background-color: {self.settings['widget-bg']}")
        self.histogramCanvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        roundedHistFrame = RoundedFrame(self, radius=10, bg_color = self.settings['widget-bg'])  # white background, 10px radius
        roundedHistFrame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        hist_shadow = QGraphicsDropShadowEffect(roundedHistFrame)
        hist_shadow.setBlurRadius(10)             # Adjust to get a softer or sharper shadow.
        hist_shadow.setColor(QColor(0, 0, 0, 120))  # Black with 150/255 opacity.
        hist_shadow.setOffset(0, 0)                # Zero offset for a symmetric shadow.
        roundedHistFrame.setGraphicsEffect(hist_shadow)
        # Create a layout for the rounded frame and add the canvas.
        histLayout = QVBoxLayout(roundedHistFrame)
        histLayout.setContentsMargins(5, 5, 5, 5)  # adjust margins as needed
        histLayout.addWidget(self.histogramCanvas)
        roundedHistFrame.setLayout(histLayout)
        rightPanelLayout.addWidget(roundedHistFrame, stretch=1)

        self.histogramCanvas.setMouseTracking(True)
        CenteredBubbleFilter(
            text="Pixel intensities within the search range",
            parent=self,
            delay_ms=1000,
            visible_ms=3000
        ).attachTo(self.histogramCanvas)

        # --- Intensity/Plot Canvas in a rounded frame ---
        self.intensityCanvas = IntensityCanvas(parent=self, navigator=self)
        self.intensityCanvas.setStyleSheet(f"background-color: {self.settings['widget-bg']}")
        self.intensityCanvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        roundedPlotFrame = RoundedFrame(self, radius=10, bg_color = self.settings['widget-bg'])
        roundedPlotFrame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        plot_shadow = QGraphicsDropShadowEffect(roundedPlotFrame)
        plot_shadow.setBlurRadius(10)
        plot_shadow.setColor(QColor(0, 0, 0, 120))
        plot_shadow.setOffset(0, 0)
        roundedPlotFrame.setGraphicsEffect(plot_shadow)
        plotLayout = QVBoxLayout(roundedPlotFrame)
        plotLayout.setContentsMargins(5, 5, 5, 5)
        plotLayout.addWidget(self.intensityCanvas)
        roundedPlotFrame.setLayout(plotLayout)
        rightPanelLayout.addWidget(roundedPlotFrame, stretch=1)

        self.intensityCanvas.setMouseTracking(True)
        # CenteredBubbleFilter(
        #     text="Spot intensities for the trajectory",
        #     parent=self,
        #     delay_ms=1000,
        #     visible_ms=3000
        # ).attachTo(self.intensityCanvas)

        # --- Velocity Canvas in a rounded frame ---
        self.velocityCanvas = VelocityCanvas(self, navigator=self)
        self.velocityCanvas.setStyleSheet(f"background-color: {self.settings['widget-bg']}")
        self.velocityCanvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        roundedVelFrame = RoundedFrame(self, radius=10, bg_color = self.settings['widget-bg'])
        roundedVelFrame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        vel_shadow = QGraphicsDropShadowEffect(roundedVelFrame)
        vel_shadow.setBlurRadius(10)
        vel_shadow.setColor(QColor(0, 0, 0, 120))
        vel_shadow.setOffset(0, 0)
        roundedVelFrame.setGraphicsEffect(vel_shadow)
        velLayout = QVBoxLayout(roundedVelFrame)
        velLayout.setContentsMargins(5, 5, 5, 5)
        velLayout.addWidget(self.velocityCanvas)
        roundedVelFrame.setLayout(velLayout)
        rightPanelLayout.addWidget(roundedVelFrame, stretch=1)

        self.velocityCanvas.setMouseTracking(True)
        CenteredBubbleFilter(
            text="Frame-to-frame speeds",
            parent=self,
            delay_ms=1000,
            visible_ms=3000
        ).attachTo(self.velocityCanvas)

        # --- Analysis Slider
        # self.analysisSlider = QSlider(Qt.Horizontal)
        # analysissliderfilter = BubbleTipFilter("Slide through trajectory points", self, placement="left")
        # self.analysisSlider.installEventFilter(analysissliderfilter)
        # self.analysisSlider._bubble_filter = analysissliderfilter
        # self.analysisSlider.setMinimum(0)
        # self.analysisSlider.setMaximum(0)  # Will be updated later when analysis data is computed
        # self.analysisSlider.setValue(0)
        # self.analysisSlider.valueChanged.connect(self.on_analysis_slider_changed)
        # self.analysisSlider.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        # rightPanelLayout.addWidget(self.analysisSlider, stretch=0)

        # rightPanelLayout.addSpacing(-2)

        # self.trajectoryControlButtons = TrajectoryControlButtons(self)
        # rightPanelLayout.addWidget(self.trajectoryControlButtons, stretch=0)

        self.rightPanel.setLayout(rightPanelLayout)
        self.topRightSplitter.addWidget(self.rightPanel)
        # Optional: adjust stretch factors for the topRightSplitter:
        self.topRightSplitter.setStretchFactor(0, 3)  # movie widget
        self.topRightSplitter.setStretchFactor(1, 2)  # right panel
        self.topRightSplitter.setCollapsible(1, True)
        self.topRightSplitter.setCollapsible(0, True)
        self.rightPanel.setVisible(False)
        self._right_panel_auto_show_pending = True
        QTimer.singleShot(0, self._collapse_right_panel_on_startup)

        # Add the top row (movie + right panel) as the upper widget in the vertical splitter.
        self.rightVerticalSplitter.addWidget(self.topRightSplitter)

        # BOTTOM of RIGHT: Trajectory Canvas that now spans the full width of the right side.
        self.trajectoryCanvas = TrajectoryCanvas(self, self.kymoCanvas, self.movieCanvas, self.intensityCanvas, navigator=self)
        self.rightVerticalSplitter.addWidget(self.trajectoryCanvas)

        self.mainSplitter.setStretchFactor(0, 1)
        self.mainSplitter.setStretchFactor(1, 2)

        self.mainSplitter.addWidget(self.rightVerticalSplitter)

        self.update_table_visibility()
        self.update_kymo_visibility()

        self.clear_button.clicked.connect(
            lambda _checked: self.trajectoryCanvas.clear_trajectories()
            )
        self.traj_overlay_button.clicked.connect(self._cycle_traj_overlay_mode)
        self.delete_button.clicked.connect(self.trajectoryCanvas.delete_selected_trajectory)
        
        
        # Connect additional signals (e.g. for mouse motion over the movie canvas).
        self.movieCanvas.mpl_connect("motion_notify_event", self.on_movie_hover)
        self.movieCanvas.mpl_connect("axes_leave_event", self.on_movie_leave)

        # Create a container (QFrame) for the zoom inset.
        self.zoomInsetFrame = QFrame(self.movieDisplayContainer)
        # Set the overall size and a rounded border.
        self.zoomInsetFrame.setMinimumWidth(140)
        self.zoomInsetFrame.setMinimumHeight(180)

        # Stash the default size.
        self._default_inset_size = (
            self.zoomInsetFrame.minimumWidth(),
            self.zoomInsetFrame.minimumHeight()
        )
        self.zoomInsetFrame.setStyleSheet("""
            QFrame {
                background: white;
                border: 2px solid transparent;
                border-radius: 10px;
            }
        """)
        insetshadow = QGraphicsDropShadowEffect(self.zoomInsetFrame)
        insetshadow.setBlurRadius(8)                  # how “soft” the shadow is
        insetshadow.setOffset(0, 0)                   # no offset—even glow
        insetshadow.setColor(QColor(0, 0, 0, 120))    # semi‑transparent black
        self.zoomInsetFrame.setGraphicsEffect(insetshadow)

        # Use a vertical layout to stack the overlay text and the image.

        zoomLayout = QVBoxLayout(self.zoomInsetFrame)
        zoomLayout.setContentsMargins(5, 5, 5, 5)
        zoomLayout.setSpacing(5)

        # Top label: no stretch, stays at its size
        self.zoomInsetLabel = QLabel("", self.zoomInsetFrame)
        self.zoomInsetLabel.setStyleSheet("""
            QLabel {
                background-color: transparent;
                color: #666666;
                border-radius: 5px;
            }
        """)
        zoomLayout.addWidget(
            self.zoomInsetLabel,
            stretch=0,
            alignment=Qt.AlignHCenter | Qt.AlignTop
        )

        # MovieCanvas inset: gets ALL extra vertical space
        self.zoomInsetWidget = MovieCanvas(parent=self.zoomInsetFrame, navigator=self)
        self.zoomInsetWidget.enableInteraction = False
        self.zoomInsetWidget.setSizePolicy(
            QSizePolicy.Expanding,
            QSizePolicy.Expanding
        )
        self.zoomInsetWidget.setMinimumSize(50, 50)  # prevents it collapsing completely
        self.zoomInsetWidget.setStyleSheet(
            "border: 5px solid white; background-color: transparent;"
        )
        zoomLayout.addWidget(
            self.zoomInsetWidget,
            stretch=1,
            alignment=Qt.AlignCenter
        )

        # Bottom intensity label: no stretch, stays at its size
        self.zoomInsetIntensityLabel = QLabel("", self.zoomInsetFrame)
        self.zoomInsetIntensityLabel.setStyleSheet("""
            QLabel {
                background-color: transparent;
                color: rgba(255, 0, 255, 130);
                border-radius: 5px;
            }
        """)
        zoomLayout.addWidget(
            self.zoomInsetIntensityLabel,
            stretch=0,
            alignment=Qt.AlignHCenter | Qt.AlignBottom
        )

        # Initially hide the zoom inset frame.
        self.zoomInsetFrame.setVisible(False)

        # Reposition the zoom inset frame when the movieDisplayContainer is resized.
        original_resize = self.movieDisplayContainer.resizeEvent
        def new_resize(event):
            original_resize(event)
            container_width = self.movieDisplayContainer.width()
            # Position the container 10 pixels from the right and top.
            self.zoomInsetFrame.move(container_width - self.zoomInsetFrame.width() - 10, 10)
        self.movieDisplayContainer.resizeEvent = new_resize

        # Add the central layout.
        central.setLayout(containerLayout)

    def resource_path(self, relative):
        if getattr(sys, 'frozen', False):
            base = sys._MEIPASS
        else:
            base = os.path.dirname(os.path.dirname(__file__))
        return os.path.join(base, relative)

    def toggle_invert_cmap(self, checked: bool):
        self.inverted_cmap = checked
        # repaint every canvas that has an _im image on it
        for canv in (self.movieCanvas,
                    self.kymoCanvas,
                    getattr(self, "zoomInsetWidget", None)):
            if canv is None: continue
            for attr in ("_im", "_im_inset"):
                im = getattr(canv, attr, None)
                if im:
                    im.set_cmap("gray_r" if checked else "gray")
            canv.draw_idle()

    def update_roilist_visibility(self):
        # Check kymo→ROI entries for imported ROIs.
        has_orphaned = any(
            info.get("orphaned", False)
            for info in self.kymo_roi_map.values()
        )
        self.roiContainer.setVisible(has_orphaned)

    def open_set_scale_dialog(self):
        # Open dialog with current values.
        self.set_scale()

    def update_scale_label(self):
        if self.pixel_size is not None and self.frame_interval is not None:
            self.scaleLabel.setText(f"{self.pixel_size:.1f} nm/pixel, {self.frame_interval:.1f} ms/frame")
        else:
            self.scaleLabel.setText("Set scale")

    def _maybe_show_load_tip(self):
        if getattr(self, "movie", None) is not None:
            return
        if getattr(self, "movieNameLabel", None) is None:
            return
        if self.movieNameLabel.text().strip().lower() != "load":
            return
        filt = getattr(self, "_load_tip_filter", None)
        if filt is None:
            return
        filt._wobj = self.movieNameLabel
        filt._showBubble(force=True)

    def update_kymo_visibility(self):
        # Check if there is neither an ROI nor a kymograph loaded.
        if self.roiCombo.count() == 0 and self.kymoCombo.count() == 0:
            # Collapse the left column by setting its width to zero.
            total_width = self.mainSplitter.width()
            self.mainSplitter.setSizes([0, total_width])
        else:
            # Otherwise, restore the left column to a default (say 20% of the total width)
            total_width = self.mainSplitter.width()
            left_width = int(0.32 * total_width)
            right_width = total_width - left_width
            self.mainSplitter.setSizes([left_width, right_width])


    def _make_colored_circle_cursor(self, size=12, thickness=2, shade='green'):
        """
        Create a smooth, anti‑aliased circular cursor in either 'green' or 'blue':
          - green → outline & fill from "#81C784"
          - blue  → outline & fill from "#7DA1FF"
        """
        # Choose hex color.
        color_map = {
            'green': '#81C784',
            'blue':  '#7DA1FF',
        }
        hex_color = color_map.get(shade.lower(), color_map['green'])

        # Create a square ARGB pixmap
        pix = QtGui.QPixmap(size, size)
        pix.fill(QtCore.Qt.transparent)

        painter = QtGui.QPainter(pix)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.setRenderHint(QtGui.QPainter.HighQualityAntialiasing, True)

        # Outline pen
        pen = QtGui.QPen(QtGui.QColor(hex_color))
        pen.setWidth(thickness)
        painter.setPen(pen)

        # Semi‑transparent fill
        fill_color = QtGui.QColor(hex_color)
        fill_color.setAlpha(80)  
        brush = QtGui.QBrush(fill_color)
        painter.setBrush(brush)

        # Draw centered circle, inset by half the pen
        radius = (size - thickness) / 2
        center = QtCore.QPointF(size / 2, size / 2)
        painter.drawEllipse(center, radius, radius)

        painter.end()

        # Return a QCursor with its hot‑spot in the center
        return QtGui.QCursor(pix, hotX=size // 2, hotY=size // 2)

    def set_roi_mode(self, enabled: bool):
        """Enable or disable ROI-add mode, updating cursor, border, and overlay button."""
        # synchronize the mode switch widget
        self.modeSwitch.setChecked(enabled)
        self.cancel_left_click_sequence()
        self.movieCanvas.roiAddMode = enabled
        if enabled:
            cursor = self._make_colored_circle_cursor(shade='green')
            self.movieCanvas.setCursor(cursor)
            self.movieDisplayContainer.setBorderColor("#81C784")
            if not self.roi_overlay_button.isChecked():
                self.roi_overlay_button.setChecked(True)
                self.toggle_roi_overlay()
        else:
            self.movieCanvas.unsetCursor()
            self.movieDisplayContainer.setBorderColor("transparent")
            if self.roi_overlay_button.isChecked():
                self.roi_overlay_button.setChecked(False)
                self.toggle_roi_overlay()

    def onModeChanged(self, mode):
        if mode == "roi":
            self.set_roi_mode(True)
        else:
            self.set_roi_mode(False)

    def flash_message(parent, text, total_ms=800, fade_ms=200):
        # 1) Create a frameless, always‑on‑top window (no Qt.ToolTip!)
        popup = QWidget(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        popup.setAttribute(Qt.WA_TranslucentBackground)      # let round‑corners show
        popup.setStyleSheet("background: transparent;")      # make sure outer is invisible

        # 2) Inner frame draws the colored, rounded rect
        frame = QFrame(popup)
        frame.setObjectName("flashFrame")
        frame.setStyleSheet("""
            QFrame#flashFrame {
                background-color: white;
                border-radius: 12px;
            }
        """)
        # drop shadow on that frame
        shadow = QGraphicsDropShadowEffect(frame)
        shadow.setBlurRadius(2)
        shadow.setOffset(0, 0)
        shadow.setColor(QColor(0,0,0,120))
        frame.setGraphicsEffect(shadow)

        # 3) put the label inside
        lbl = QLabel(text, frame)
        lbl.setStyleSheet("color: black; font-size: 14px;")
        lay = QHBoxLayout(frame)
        lay.setContentsMargins(12,6,12,6)
        lay.addWidget(lbl)

        # 4) zero‑margin layout on popup
        top_lay = QHBoxLayout(popup)
        top_lay.setContentsMargins(0,0,0,0)
        top_lay.addWidget(frame)
        popup.adjustSize()

        # 5) center it over the parent
        pw, ph = parent.width(), parent.height()
        lw, lh = popup.width(), popup.height()
        pos = parent.mapToGlobal(QPoint((pw-lw)//2, (ph-lh)//62))
        popup.move(pos)

        popup.show()

        # 6) fade out via windowOpacity
        def start_fade():
            anim = QPropertyAnimation(popup, b"windowOpacity", popup)
            anim.setDuration(fade_ms)
            anim.setStartValue(1.0)
            anim.setEndValue(0.0)
            anim.finished.connect(popup.deleteLater)
            anim.start()
            popup._anim = anim

        QTimer.singleShot(total_ms - fade_ms, start_fade)

        # 7) fade out by animating the widget’s windowOpacity (not via another effect)
        def start_fade():
            anim = QPropertyAnimation(popup, b"windowOpacity", popup)
            anim.setDuration(fade_ms)
            anim.setStartValue(1.0)
            anim.setEndValue(0.0)
            anim.finished.connect(popup.deleteLater)
            anim.start()
            popup._anim = anim  # keep reference alive

        QTimer.singleShot(total_ms - fade_ms, start_fade)

    def delete_current_roi(self):
        current = self.roiCombo.currentText()
        if current:
            # Remove from the dictionary.
            if current in self.rois:
                del self.rois[current]
            # Remove from the combo box.
            index = self.roiCombo.currentIndex()
            self.roiCombo.removeItem(index)
            self.kymo_roi_map.pop(current, None)
            # Optionally, reset the selection if needed.
            if self.roiCombo.count() > 0:
                self.roiCombo.setCurrentIndex(0)
            # Also update any ROI overlays if they are active.
            self.update_roi_overlay_if_active()
            self.update_roilist_visibility()

    def get_traj_overlay_mode(self):
        return getattr(self, "traj_overlay_mode", "all")

    def _traj_overlay_has_selection(self):
        tc = getattr(self, "trajectoryCanvas", None)
        if tc is None or not tc.trajectories:
            return False
        sel = tc.table_widget.selectionModel()
        if sel is None:
            return False
        return bool(sel.selectedRows())

    def _normalize_traj_overlay_mode(self, mode):
        if mode not in ("off", "selected", "all"):
            mode = "all"
        return mode

    def _apply_traj_overlay_mode(self, mode, redraw=True):
        mode = self._normalize_traj_overlay_mode(mode)
        self.traj_overlay_mode = mode
        icon = self._traj_overlay_icons["selected"] if mode == "selected" else self._traj_overlay_icons["all"]
        self.traj_overlay_button.setIcon(icon)
        self.traj_overlay_button.setChecked(mode != "off")
        if redraw and getattr(self, "trajectoryCanvas", None) is not None:
            self.trajectoryCanvas.toggle_trajectory_markers()

    def _cycle_traj_overlay_mode(self):
        if getattr(self, "trajectoryCanvas", None) is None:
            return
        order = ("off", "all", "selected")
        current = self.get_traj_overlay_mode()
        try:
            idx = order.index(current)
        except ValueError:
            idx = 0
        for step in range(1, len(order) + 1):
            candidate = order[(idx + step) % len(order)]
            self._apply_traj_overlay_mode(candidate)
            return

    def _ensure_traj_overlay_mode_valid(self, redraw=False):
        current = self.get_traj_overlay_mode()
        normalized = self._normalize_traj_overlay_mode(current)
        if normalized != current:
            self._apply_traj_overlay_mode(normalized, redraw=redraw)
        elif redraw and getattr(self, "trajectoryCanvas", None) is not None:
            self.trajectoryCanvas.toggle_trajectory_markers()

    def _on_o_pressed(self):
        if len(self.trajectoryCanvas.trajectories) == 0:
            return
        self._cycle_traj_overlay_mode()

    def _on_m_pressed(self):
        if self.movie is None:
            return
        self.sumBtn.toggle()

    def _on_n_pressed(self):
        if self.movie is None:
            return
        is_roi = self.modeSwitch.isChecked()
        if is_roi:
            self.set_roi_mode(False)
        else:
            if self.looping:
                self.stoploop()
            self.set_roi_mode(True)


    def handleGlobalArrow(self, key):

        now = time.perf_counter()
        if now - self._last_arrow < self._arrow_cooldown:
            return
        self._last_arrow = now

        if self.movie is None:
            return

        # If we were looping, stop it
        if self.looping:
            self.stoploop()

        # Only handle Left/Right here
        if key in (Qt.Key_Right, Qt.Key_Left) and self.analysis_frames:
            # pick direction (+1 or -1)
            direction = 1 if key == Qt.Key_Right else -1

            # compute and store the new index
            N = len(self.analysis_frames)
            self.loop_index = (self.intensityCanvas.current_index + direction) % N
            self.intensityCanvas.current_index = self.loop_index

            # Use the same call as the loop.
            #   pan instantly, repaint fully, draw the X
            self.jump_to_analysis_point(self.loop_index, animate="discrete")

            return

        table = self.trajectoryCanvas.table_widget
        row_count = table.rowCount()
        if row_count == 0:
            return

        current = table.currentRow()
        if current < 0:
            current = 0

        if key == Qt.Key_Down:
            new_row = (current + 1) % row_count
        elif key == Qt.Key_Up:
            new_row = (current - 1) % row_count

        table.selectRow(new_row)

    def keyPressEvent(self, event):
        # Ctrl/Cmd+Arrow: translate reference image (persist across ref toggles)
        try:
            if (event.modifiers() & (Qt.ControlModifier | Qt.MetaModifier)) and hasattr(self, "refBtn") and self.refBtn.isChecked():
                step = 1
                if event.key() == Qt.Key_Left:
                    self._nudge_reference_translation(-step, 0)
                    return
                if event.key() == Qt.Key_Right:
                    self._nudge_reference_translation(step, 0)
                    return
                if event.key() == Qt.Key_Down:
                    self._nudge_reference_translation(0, -step)
                    return
                if event.key() == Qt.Key_Up:
                    self._nudge_reference_translation(0, step)
                    return
        except Exception:
            pass

        if event.key() == Qt.Key_Shift:
            try:
                self.cancel_left_click_sequence()
                self._set_kymo_anchor_edit_mode(True)
            except Exception:
                pass

        # Preserve existing key handling.
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key_Shift:
            try:
                self._finish_kymo_anchor_edit(force_recalc=False)
            except Exception:
                pass
            super().keyReleaseEvent(event)
            return
        super().keyReleaseEvent(event)

    def _showRadiusDialog(self):
        # don’t open if it’s already up
        if self._radiusPopup:
            return

        dlg = RadiusDialog(self.searchWindowSpin.value(), self)
        dlg.adjustSize()
        pw, ph = self.width(), self.height()
        lw, lh = dlg.width(), dlg.height()
        x = (pw - lw) // 2
        y = (ph - lh) // 90
        dlg.move(self.mapToGlobal(QPoint(x, y)))
        dlg.show()

        self._radiusPopup    = dlg
        self._radiusSpinLive = dlg._spin

    def handleGlobalX(self):
        """
        Global handler for the X key: invalidate or re-validate the current point,
        update all analysis buffers, trajectory model, table UI, and redraw everything.
        """
        # ── 0) Basic guards ──
        if not getattr(self, "intensityCanvas", None) or not self.intensityCanvas.point_highlighted:
            return
        if not self.analysis_frames or not self.analysis_original_coords:
            return
        if self.looping:
            self.stoploop()

        # ── 1) Figure out which point & trajectory row we’re on ──
        idx = self.intensityCanvas.current_index
        row_sel = self.trajectoryCanvas.table_widget.selectionModel().selectedRows()
        if not row_sel:
            return
        row = row_sel[0].row()
        traj = self.trajectoryCanvas.trajectories[row]

        # ── 2) Capture “was valid?” before we clobber it ──
        was_valid = (self.analysis_intensities[idx] is not None)

        # ── 3) Prepare some locals ──
        frame       = self.analysis_frames[idx]
        search_ctr  = self.analysis_search_centers[idx]
        crop_size   = int(2 * self.searchWindowSpin.value())
        frame_image = self.get_movie_frame(frame)

        # ── 4) Invalidate vs re-validate ──
        if was_valid:
            # ---- invalidation: clear fit & intensities ----
            self.analysis_fit_params[idx] = (None, None, None)
            self.analysis_intensities[idx]  = None

            # clear any colocalization flags
            if getattr(self, "check_colocalization", False) and self.movie.ndim == 4:
                traj["colocalization_any"][idx] = None
                for lst in traj["colocalization_by_ch"].values():
                    lst[idx] = None
                # also wipe navigator’s buffers so percentages recalc skip this point
                self.analysis_colocalized[idx] = False
                for lst in self.analysis_colocalized_by_ch.values():
                    lst[idx] = False

            self.movieCanvas.remove_gaussian_circle()
            self.movieCanvas.remove_inset_circle()

            self.flash_message("Remove")
            # (marker drawing moved below)

        else:
            # ---- re-analysis: Gaussian fit + recalc colocalization ----
            fitted, sigma, intensity, peak, bkgr = perform_gaussian_fit(
                frame_image, search_ctr, crop_size,
                pixelsize=self.pixel_size,
                bg_fixed=self.analysis_background[idx]
            )
            self.analysis_fit_params[idx] = (fitted, sigma, peak)
            self.analysis_intensities[idx] = intensity

            # re-run colocalization for this frame if needed
            if getattr(self, "check_colocalization", False) and self.movie.ndim == 4:
                # pull back the trajectory’s dict
                traj = self.trajectoryCanvas.trajectories[row]

                # 1) compute flags for this single frame & its fitted center
                any_flag, per_ch = self._coloc_flags_for_frame(frame, fitted)

                # 2) store in trajectory and navigator buffers
                traj["colocalization_any"][idx] = any_flag
                self.analysis_colocalized[idx] = any_flag

                for ch, flag in per_ch.items():
                    traj["colocalization_by_ch"][ch][idx] = flag
                    self.analysis_colocalized_by_ch[ch][idx] = flag

            self.flash_message("Reattempt")
            # (marker drawing moved below)

        # ── 5) Recompute velocities, averages, etc. ──
        centers = [p for p,_,_ in self.analysis_fit_params]
        self.analysis_velocities = calculate_velocities(centers)
        # avg velocity (px/frame)
        vels = [v for v in self.analysis_velocities if v is not None]
        self.average_velocity = float(np.mean(vels)) if vels else None
        # avg & median intensity
        ints = [v for v in self.analysis_intensities if v not in (None, 0)]
        self.analysis_avg    = float(np.mean(ints))   if ints else None
        self.analysis_median = float(np.median(ints)) if ints else None

        # ── 6) Update trajectory dict with new arrays ──
        traj["spot_centers"][idx] = self.analysis_fit_params[idx][0]
        traj["sigmas"][idx]       = self.analysis_fit_params[idx][1]
        traj["peaks"][idx]        = self.analysis_fit_params[idx][2]
        traj["intensities"][idx]  = self.analysis_intensities[idx]
        traj["velocities"]        = self.analysis_velocities
        traj["average_velocity"]  = self.average_velocity
        traj["average"]           = self.analysis_avg
        traj["median"]            = self.analysis_median
        traj["background"]        = self.analysis_background

        # ── 7) Rewrite the entire row of the table ──
        # valid %
        pct_valid = int(
            100 * len(ints) /
            len(self.analysis_frames)
        ) if self.analysis_frames else 0
        self.trajectoryCanvas.writeToTable(row, "valid", str(pct_valid))
        # median intensity
        medtxt = "" if self.analysis_median is None else f"{self.analysis_median:.2f}"
        self.trajectoryCanvas.writeToTable(row, "medintensity", medtxt)

        # colocalization % columns
        if getattr(self, "check_colocalization", False) and self.movie.ndim == 4:
            n_chan = self.movie.shape[self._channel_axis]
            # compute new percentages
            any_flags = traj["colocalization_any"]
            valid_any = [s for s in any_flags if s is not None]
            pct_any   = (
                f"{100*sum(1 for s in valid_any if s=='Yes')/len(valid_any):.1f}"
                if valid_any else ""
            )
            by_ch = traj["colocalization_by_ch"]
            pct_by_ch = {
                ch: (
                    f"{100*sum(1 for s in lst if s=='Yes')/len([s for s in lst if s is not None]):.1f}"
                    if any(s is not None for s in lst) else ""
                )
                for ch, lst in by_ch.items()
            }

            for ch in range(1, n_chan+1):
                hdr = f"Ch. {ch} co. %"
                if ch == traj["channel"]:
                    val = ""
                elif n_chan == 2:
                    val = pct_any
                else:
                    val = pct_by_ch.get(ch, "")
                self.trajectoryCanvas._mark_custom(row, hdr, val)

         # ── 7.5) Steps ──
        if getattr(self, "show_steps", False):
            # compute_steps_for_data should return (step_indices, step_medians).
            step_idxs, step_meds = self.compute_steps_for_data(
                self.analysis_frames,
                self.analysis_intensities,
            )
            traj["step_indices"] = step_idxs
            traj["step_medians"] = step_meds
        else:
            # if steps aren’t shown, clear them out
            traj["step_indices"] = None
            traj["step_medians"] = None

        # ── 7.6) Diffusion (D, α) ──
        if getattr(self, "show_diffusion", False):
            centers = [p for p,_,_ in self.analysis_fit_params]
            D, alpha = self.compute_diffusion_for_data(self.analysis_frames, centers)

            D_COL = self._DIFF_D_COL
            A_COL = self._DIFF_A_COL
            traj.setdefault("custom_fields", {})
            traj["custom_fields"][D_COL] = "" if D is None else f"{D:.4g}"
            traj["custom_fields"][A_COL] = "" if alpha is None else f"{alpha:.3f}"

            # update table cells (no canvas refresh needed)
            if D_COL in self.trajectoryCanvas.custom_columns:
                self.trajectoryCanvas.writeToTable(row, D_COL, traj["custom_fields"][D_COL])
            if A_COL in self.trajectoryCanvas.custom_columns:
                self.trajectoryCanvas.writeToTable(row, A_COL, traj["custom_fields"][A_COL])

            if self.pixel_size is not None and self.frame_interval is not None:
                try:
                    traj["segment_diffusion"] = self.trajectoryCanvas._compute_segment_diffusion(
                        traj, self
                    )
                except Exception:
                    traj["segment_diffusion"] = []
        else:
            # optional: blank them
            D_COL = self._DIFF_D_COL
            A_COL = self._DIFF_A_COL
            traj.setdefault("custom_fields", {})
            traj["custom_fields"][D_COL] = ""
            traj["custom_fields"][A_COL] = ""

        # ── 8) Refresh all canvases ──
        self.kymoCanvas.update_view()
        self.kymoCanvas.clear_kymo_trajectory_markers()
        self.kymoCanvas.remove_circle()

        # re-draw overlays if toggled
        self.movieCanvas.draw_trajectories_on_movie()
        self.kymoCanvas.draw_trajectories_on_kymo()

        # re-draw intensity / velocity plots
        self.intensityCanvas.plot_intensity(
            self.analysis_frames,
            self.analysis_intensities,
            avg_intensity=self.analysis_avg,
            median_intensity=self.analysis_median,
            colors=self._get_traj_colors(traj)[0]
        )
        self.intensityCanvas.highlight_current_point()
        self.velocityCanvas.plot_velocity_histogram(self.analysis_velocities)

        # ── 9) Draw the invalidation/validation circles ──
        if was_valid:
            # grey on kymo at search center
            sx, sy = search_ctr
            kymo_name = self.kymoCombo.currentText()
            if kymo_name and kymo_name in self.kymographs and self.rois:
                roi = self.rois[self.roiCombo.currentText()]
                xk = self.compute_kymo_x_from_roi(
                    roi, sx, sy,
                    self.kymographs[kymo_name].shape[1]
                )
                if xk is not None:
                    disp_frame = (self.movie.shape[0] - 1) - frame
                    self.kymoCanvas.add_circle(xk, disp_frame, color='grey')
        else:
            # magenta on movie at fitted center
            if fitted is not None:
                pointcolor = self.get_point_color()
                self.movieCanvas.add_gaussian_circle(fitted, sigma, pointcolor)
                center_for_zoom = fitted if fitted is not None else search_ctr
                self.movieCanvas.update_inset(
                    frame_image, center_for_zoom, int(self.insetViewSize.value()), 2,
                    fitted_center=fitted,
                    fitted_sigma=sigma,
                    fitted_peak=peak,
                    intensity_value=intensity,
                    offset = bkgr,
                    pointcolor = pointcolor
                )
            # magenta on kymo at fitted center (or search if fit failed)
            fx, fy = (fitted if fitted is not None else search_ctr)
            kymo_name = self.kymoCombo.currentText()
            if kymo_name and kymo_name in self.kymographs and self.rois:
                roi = self.rois[self.roiCombo.currentText()]
                xk = self.compute_kymo_x_from_roi(
                    roi, fx, fy,
                    self.kymographs[kymo_name].shape[1]
                )
                if xk is not None:
                    disp_frame = (self.movie.shape[0] - 1) - frame
                    self.kymoCanvas.add_circle(xk, disp_frame, color=self.get_point_color())

        # final idle draws
        self.zoomInsetWidget.draw_idle()
        self.kymoCanvas.draw_idle()
        self.movieCanvas.draw_idle()

    def get_point_color(self):
        return self.intensityCanvas.get_current_point_color()

    # In create_menu(), add a new menu action:
    def create_menu(self):
        menubar = self.menuBar()

        # Create File-related menus
        loadMenu = menubar.addMenu("Load")

        loadMovieAction = QAction("Movie", self)
        loadMovieAction.triggered.connect(self.handle_movie_load)
        loadMenu.addAction(loadMovieAction)

        loadROIsAction = QAction("Line ROIs", self)
        loadROIsAction.triggered.connect(self.load_roi)
        loadMenu.addAction(loadROIsAction)
        
        loadTrajectoriesAction = QAction("Trajectories", self)
        loadTrajectoriesAction.triggered.connect(self.trajectoryCanvas.load_trajectories)
        loadMenu.addAction(loadTrajectoriesAction)
        
        loadReferenceAction = QAction("Reference", self)
        loadReferenceAction.triggered.connect(self.load_reference)
        loadMenu.addAction(loadReferenceAction)
        
        # loadKymosAction = QAction("Kymograph w/Point-ROIs", self)
        # loadKymosAction.triggered.connect(self.load_kymograph_with_overlays)
        # loadMenu.addAction(loadKymosAction)

        loadTrackMateAction = QAction("TrackMate spots", self)
        loadTrackMateAction.triggered.connect(self.trajectoryCanvas.load_trackmate_spots)
        loadMenu.addAction(loadTrackMateAction)

        saveMenu = menubar.addMenu("Save")
        saveTrajectoriesAction = QAction("Trajectories", self)
        saveTrajectoriesAction.setShortcut(QKeySequence.Save)
        saveTrajectoriesAction.triggered.connect(lambda: self.trajectoryCanvas.save_trajectories())
        saveMenu.addAction(saveTrajectoriesAction)

        saveKymosAction = QAction("Kymographs", self)
        saveKymosAction.triggered.connect(self.save_kymographs)
        saveMenu.addAction(saveKymosAction)

        # saveAllKymosAction = QAction("Kymographs (all)", self)
        # saveAllKymosAction.triggered.connect(partial(self.save_kymographs, False))
        # saveMenu.addAction(saveAllKymosAction)

        # saveKymosOverlaysAction = QAction("Kymographs w/Overlays", self)
        # saveKymosOverlaysAction.triggered.connect(self.save_kymographs_with_overlays)
        # saveMenu.addAction(saveKymosOverlaysAction)

        saveROIsAction = QAction("Line ROIs", self)
        saveROIsAction.triggered.connect(self.save_rois)
        saveMenu.addAction(saveROIsAction)

        movieMenu = menubar.addMenu("Movie")
        correctDriftAction = QAction("Correct Drift", self)
        correctDriftAction.triggered.connect(self.correct_drift)
        movieMenu.addAction(correctDriftAction)

        setScaleAction = QAction("Set Scale", self)
        setScaleAction.triggered.connect(self.set_scale)
        movieMenu.addAction(setScaleAction)

        channelAxisAction = QAction("Change Channel Axis", self)
        channelAxisAction.triggered.connect(self.show_channel_axis_dialog)
        channelAxisAction.setEnabled(False)
        self.channelAxisAction = channelAxisAction
        movieMenu.addAction(channelAxisAction)

        self.spotMenu = menubar.addMenu("Spot")
        searchRadiusAction = QAction("Search Radius", self)
        searchRadiusAction.triggered.connect(self.set_search_radius)
        self.spotMenu.addAction(searchRadiusAction)

        # Create a QComboBox for Tracking Mode and wrap it in a QWidgetAction.
        trackingModeAction = QAction("Tracking Mode", self)
        trackingModeAction.triggered.connect(self.set_tracking_mode)
        self.spotMenu.addAction(trackingModeAction)

        avoidOldSpotsAction = QAction("Avoid previous spots", self, checkable=True)
        avoidOldSpotsAction.setChecked(False)
        avoidOldSpotsAction.setStatusTip("Skip any spot centers that were already analysed")
        avoidOldSpotsAction.toggled.connect(lambda checked: setattr(self, "avoid_previous_spot", checked))
        self.spotMenu.addAction(avoidOldSpotsAction)

        kymoMenu = menubar.addMenu("Kymograph")

        kymoLoGAction = QAction("Apply LoG filter", self, checkable=True)
        kymoLoGAction.setChecked(False)
        kymoLoGAction.toggled.connect(self.on_toggle_log_filter)
        kymoMenu.addAction(kymoLoGAction)

        kymopreferencesAction = QAction("Line options", self)
        kymopreferencesAction.triggered.connect(self.open_kymopreferences_dialog)
        kymoMenu.addAction(kymopreferencesAction)

        kymoGenerateFromTrajAction = QAction("Draw from trajectories", self)
        kymoGenerateFromTrajAction.triggered.connect(self.generate_rois_from_trajectories)
        kymoMenu.addAction(kymoGenerateFromTrajAction)

        connectgapsAction = QAction("Connect spot gaps", self, checkable=True)
        connectgapsAction.setChecked(False)
        connectgapsAction.toggled.connect(self.on_connect_spot_gaps_toggled)
        kymoMenu.addAction(connectgapsAction)

        trajMenu = menubar.addMenu("Trajectories")

        recalcAction = QAction("Recalculate selected", self)
        recalcAction.triggered.connect(self.trajectoryCanvas.recalculate_trajectory)
        trajMenu.addAction(recalcAction)

        recalcAction = QAction("Recalculate all", self)
        recalcAction.triggered.connect(self.trajectoryCanvas.recalculate_all_trajectories)
        trajMenu.addAction(recalcAction)

        binColumnAct = QAction("Add Binary column", self)
        trajMenu.addAction(binColumnAct)
        binColumnAct.triggered.connect(self.trajectoryCanvas._add_binary_column_dialog)
        
        valColumnAct = QAction("Add Value column", self)
        trajMenu.addAction(valColumnAct)
        valColumnAct.triggered.connect(self.trajectoryCanvas._add_value_column_dialog)

        self._add_extra_calc_actions(trajMenu)

        self._colorBySeparator = trajMenu.addSeparator()
        self.colorByMenu = QMenu("Color By", self)
        trajMenu.addMenu(self.colorByMenu)
        self._colorByActions   = []
        self.trajMenu          = trajMenu

        viewMenu = menubar.addMenu("View")

        self.invertAct = QAction("Invert", self, checkable=True)
        self.invertAct.triggered.connect(self.toggle_invert_cmap)
        viewMenu.addAction(self.invertAct)

        zoomAction = QAction("Inset size", self)
        zoomAction.triggered.connect(self.open_zoom_dialog)
        viewMenu.addAction(zoomAction)

        shortcutsAction = QAction("Shortcuts", self)
        shortcutsAction.triggered.connect(self.open_shortcuts_dialog)
        viewMenu.addAction(shortcutsAction)

    def _add_extra_calc_actions(self, traj_menu):
        self._extra_calc_actions = {}
        for spec in self._extra_calc_specs():
            action = QAction(spec.label, self, checkable=True)
            action.setChecked(False)
            handler = getattr(self, spec.toggle_handler)
            action.toggled.connect(handler)

            if spec.has_popup or spec.checks_existing:
                tips = []
                if spec.has_popup:
                    tips.append("Opens settings")
                if spec.checks_existing:
                    tips.append("Can prompt to compute missing values")
                action.setStatusTip(". ".join(tips))

            if spec.key == "colocalization":
                action.setEnabled(False)

            setattr(self, spec.action_attr, action)
            traj_menu.addAction(action)
            self._extra_calc_actions[spec.key] = action

    def open_shortcuts_dialog(self):
        dialog = ShortcutsDialog(self)
        dialog.exec_()

    def _rebuild_color_by_actions(self):
        # 1) clear old
        for act in self._colorByActions:
            self.colorByMenu.removeAction(act)
        self._colorByActions.clear()

        def finalize_menu():
            def sort_key(act):
                return act.text().casefold().replace("α", "a")
            self._colorByActions.sort(key=sort_key)
            for act in self._colorByActions:
                self.colorByMenu.addAction(act)
            has_actions = bool(self._colorByActions)
            self.colorByMenu.menuAction().setVisible(has_actions)
            self.colorByMenu.setEnabled(has_actions)

        # 2) add custom columns (binary/value)
        for col in self.trajectoryCanvas.custom_columns:
            ctype = self.trajectoryCanvas._column_types[col]
            if ctype in ("binary", "value"):
                act = QAction(f"{col}", self, checkable=True)
                act.setData(col)   # ← store the real key
                act.toggled.connect(lambda on, a=act: 
                    self._on_color_by_toggled(a.data(), a, on)
                )
                # if already selected, show its checkmark
                if self.color_by_column == col:
                    act.setChecked(True)

                self._colorByActions.append(act)

        if self.movie is None:
            finalize_menu()
            return

        has_seg_diff = any(
            isinstance(t.get("segment_diffusion"), (list, tuple)) and t.get("segment_diffusion")
            for t in self.trajectoryCanvas.trajectories
        )
        if getattr(self, "show_diffusion", False) or has_seg_diff:
            d_col = getattr(self, "_DIFF_D_COL", "D (μm²/s)")
            a_col = getattr(self, "_DIFF_A_COL", "α")
            for base in (d_col, a_col):
                key = f"{base} (per segment)"
                act = QAction(f"{key}", self, checkable=True)
                act.setData(key)
                act.toggled.connect(lambda on, a=act: 
                    self._on_color_by_toggled(a.data(), a, on)
                )
                if self.color_by_column == key:
                    act.setChecked(True)
                self._colorByActions.append(act)

        # 3) count channels
        if self.movie.ndim == 4 and self._channel_axis is not None:
            n_chan = self.movie.shape[self._channel_axis]
        else:
            n_chan = 1

        # 4) add colocalization actions (only when enabled)
        if getattr(self, "check_colocalization", False):
            if n_chan == 2:
                key = "colocalization"
                act = QAction("Colocalization", self, checkable=True)
                act.setData(key)
                act.toggled.connect(lambda on, a=act: 
                    self._on_color_by_toggled(a.data(), a, on)
                )
                if self.color_by_column == key:
                    act.setChecked(True)
                self._colorByActions.append(act)

            elif n_chan > 2:
                for tgt in range(1, n_chan+1):
                    key  = f"coloc_ch{tgt}"
                    text = f"Ch. {tgt} coloc"
                    act = QAction(text, self, checkable=True)
                    act.setData(key)
                    act.toggled.connect(lambda on, a=act: 
                        self._on_color_by_toggled(a.data(), a, on)
                    )
                    if self.color_by_column == key:
                        act.setChecked(True)
                    self._colorByActions.append(act)

        finalize_menu()

    def _on_color_by_toggled(self, column_name, action, checked):
        if checked and (column_name == "colocalization" or column_name.startswith("coloc_ch")):
            # colocalizationAction is the earlier QAction.
            if not self.colocalizationAction.isChecked():
                # This checks the box and fires on_colocalization_toggled.
                self.colocalizationAction.setChecked(True)

        if checked:
            # uncheck the other color‐by actions
            for act in self._colorByActions:
                if act is not action:
                    act.setChecked(False)
            self.set_color_by(column_name)
        else:
            # Clear color-by when untoggled.
            self.set_color_by(None)

    def open_kymopreferences_dialog(self):
        """
        Open a preferences dialog to adjust settings such as the ROI line width
        and the integration method used when sampling the ROI line.
        """
        # Get current values (default line width 2 and method "max")
        current_line_width = getattr(self, "line_width", 2)
        current_method = getattr(self, "line_integration_method", "max")
        
        dialog = KymoLineOptionsDialog(current_line_width, current_method, self)
        if dialog.exec_() == QDialog.Accepted:
            line_width, method = dialog.getValues()
            self.line_width = line_width
            self.line_integration_method = method

    def set_search_radius(self):
        current_radius = self.searchWindowSpin.value()
        dialog = QInputDialog(self)
        dialog.setWindowTitle("Set Search Radius")
        dialog.setLabelText(
            "<b>Search Radius</b><br>"
            "<i>shortcut: r + scroll</i>"
        )
        dialog.setIntRange(4, 50)
        dialog.setIntValue(current_radius)

        # Center the label vertically & horizontally
        label = dialog.findChild(QLabel)
        if label:
            label.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)

        if dialog.exec_() == QDialog.Accepted:
            self.searchWindowSpin.setValue(dialog.intValue())

    def set_tracking_mode(self):
        current_mode = getattr(self, "tracking_mode", "Independent")
        options = ["Independent", "Tracked", "Smooth"] #, "Same center"

        dialog = QInputDialog(self)
        dialog.setWindowTitle("Set Tracking Mode")
        dialog.setLabelText(
            "<b>Tracking Mode</b><br>"
            "<i>shortcut: t</i>"
        )
        dialog.setComboBoxItems(options)
        dialog.setComboBoxEditable(False)
        dialog.setTextValue(current_mode)
        dialog.setStyleSheet(QApplication.instance().styleSheet())

        # Center the label vertically & horizontally
        label = dialog.findChild(QLabel)
        if label:
            label.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)

        if dialog.exec_() == QDialog.Accepted:
            new_mode = dialog.textValue()
            self.tracking_mode = new_mode
            if hasattr(self, "trackingModeCombo"):
                self.trackingModeCombo.setCurrentText(new_mode)

    def open_zoom_dialog(self):
        # Create a modal dialog to adjust the zoom window size.
        dialog = QDialog(self)
        dialog.setWindowTitle("Set Inset Size")
        layout = QVBoxLayout(dialog)
        
        # Spinbox to choose the value.
        spinbox = QSpinBox(dialog)
        spinbox.setRange(4, 50)
        spinbox.setValue(self.insetViewSize.value())
        layout.addWidget(spinbox)
        
        # Add Set and Cancel buttons.
        buttonLayout = QHBoxLayout()
        setButton = QPushButton("Set", dialog)
        cancelButton = QPushButton("Cancel", dialog)
        buttonLayout.addWidget(setButton)
        buttonLayout.addWidget(cancelButton)
        layout.addLayout(buttonLayout)
        
        # Connect buttons.
        setButton.clicked.connect(lambda: self.set_zoom_window_size(spinbox.value(), dialog))
        cancelButton.clicked.connect(dialog.reject)

        # when they click "Set" we'll 1) commit the new size, 2) close the dialog, 
        # 3) and then force a redraw of the inset using our last‐seen parameters
        def on_set():
            # 1) commit the new window size and close the dialog
            self.set_zoom_window_size(spinbox.value(), dialog)

            # 2) grab the old inset parameters
            image, center, old_crop, zoom_factor, fcenter, fsigma, fpeak, bkgr, ivalue, pointcolor = \
                self.movieCanvas._last_inset_params

            # 3) re‑fire update_inset with the NEW crop_size
            self.movieCanvas.update_inset(
                image, center, spinbox.value(), zoom_factor,
                fitted_center=fcenter,
                fitted_sigma=fsigma,
                fitted_peak=fpeak,
                intensity_value=ivalue,
                offset = bkgr,
                pointcolor = pointcolor
            )

        setButton.clicked.connect(on_set)

        dialog.exec_()

    def set_zoom_window_size(self, value, dialog):
        # Update the insetViewSize value and close the dialog.
        self.insetViewSize.setValue(value)
        dialog.accept()

    def on_connect_spot_gaps_toggled(self, checked: bool):
        # Store on navigator (used by kymo drawing).
        self.connect_all_spots = checked
        # then force a redraw
        self.kymoCanvas.draw_trajectories_on_kymo()
        self.kymoCanvas.draw_idle()

    def on_toggle_log_filter(self, checked: bool):
        self.applylogfilter = checked

    def on_colocalization_toggled(self, checked: bool):
        if self.movie is None or self.movie.ndim != 4:
            self.check_colocalization = False
            self._rebuild_color_by_actions()
            return

        self.check_colocalization = checked
        if not checked:
            if self.color_by_column in ("colocalization",) or str(self.color_by_column).startswith("coloc_ch"):
                self.set_color_by(None)
            self._rebuild_color_by_actions()
            return
        
        missing = self._find_missing_colocalization()
        if not missing:
            self._rebuild_color_by_actions()
            return

        if not self._confirm_missing_calculation("colocalization", len(missing)):
            self._rebuild_color_by_actions()
            return

        self._compute_colocalization_for_indices(missing)

        # finally redraw
        self.kymoCanvas.draw_trajectories_on_kymo()
        self.kymoCanvas.draw()
        self.movieCanvas.draw_trajectories_on_movie()
        self.movieCanvas.draw()

        self.trajectoryCanvas.hide_empty_columns()
        self._rebuild_color_by_actions()

    def _schedule_update_check(self):
        if getattr(self, "_update_check_started", False):
            return
        if os.environ.get("TRACY_DISABLE_UPDATE_CHECK", "").lower() in ("1", "true", "yes"):
            return

        self._update_check_started = True
        self._update_check_executor = ThreadPoolExecutor(max_workers=1)
        self._update_check_future = self._update_check_executor.submit(self._fetch_latest_version)
        self._update_check_timer = QTimer(self)
        self._update_check_timer.setInterval(200)
        self._update_check_timer.timeout.connect(self._poll_update_check)
        self._update_check_timer.start()

    def _poll_update_check(self):
        future = getattr(self, "_update_check_future", None)
        if not future or not future.done():
            return
        if hasattr(self, "_update_check_timer"):
            self._update_check_timer.stop()
        latest = None
        try:
            latest = future.result()
        except Exception:
            latest = None
        self._update_check_future = None
        if hasattr(self, "_update_check_executor"):
            self._update_check_executor.shutdown(wait=False)

        if not latest:
            return

        current = self._current_version()
        if not current or not self._is_newer_version(latest, current):
            return

        self._show_update_bubble(latest)

    def _fetch_latest_version(self):
        import json
        import urllib.request
        import urllib.error

        url = "https://pypi.org/pypi/tracyspot/json"
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                if resp.status != 200:
                    return None
                payload = json.load(resp)
        except Exception:
            return None
        return payload.get("info", {}).get("version")

    def _current_version(self):
        try:
            import tracy
            return tracy.__version__
        except Exception:
            return ""

    def _is_newer_version(self, latest, current):
        try:
            from packaging.version import Version
            return Version(latest) > Version(current)
        except Exception:
            try:
                from distutils.version import LooseVersion
                return LooseVersion(latest) > LooseVersion(current)
            except Exception:
                return latest != current

    def _show_update_bubble(self, latest_version):
        if getattr(self, "_update_bubble", None) is not None:
            return
        if self.width() == 0 or self.height() == 0:
            QTimer.singleShot(500, lambda: self._show_update_bubble(latest_version))
            return

        message = (
            f"Version {latest_version} ready, please update with "
            "pip install tracyspot --upgrade"
        )
        bubble = QFrame(self, Qt.ToolTip | Qt.FramelessWindowHint)
        bubble.setAttribute(Qt.WA_ShowWithoutActivating)
        bubble.setAttribute(Qt.WA_TranslucentBackground)
        bubble.setStyleSheet(
            "QFrame {"
            "background-color: rgba(255, 255, 255, 235);"
            "border: 1px solid #d0d0d0;"
            "border-radius: 10px;"
            "}"
            "QLabel {"
            "background: white;"
            "}"
        )

        label = QLabel(message, bubble)
        label.setWordWrap(False)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        label.setStyleSheet("font-weight: normal;")

        layout = QVBoxLayout(bubble)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.addWidget(label)
        bubble.adjustSize()

        margin = 12
        top_right = self.mapToGlobal(self.rect().topRight())
        x = top_right.x() - bubble.width() - margin
        y = top_right.y() + margin
        bubble.move(x, y)
        bubble.show()

        self._update_bubble = bubble
        QTimer.singleShot(8000, self._hide_update_bubble)

    def _hide_update_bubble(self):
        bubble = getattr(self, "_update_bubble", None)
        if bubble is None:
            return
        bubble.hide()
        bubble.deleteLater()
        self._update_bubble = None
