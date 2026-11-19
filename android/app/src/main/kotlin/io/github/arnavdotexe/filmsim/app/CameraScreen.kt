package io.github.arnavdotexe.filmsim.app

import android.content.Context
import android.graphics.Bitmap
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.net.Uri
import android.util.Log
import android.util.Rational
import android.view.OrientationEventListener
import android.view.Surface
import androidx.camera.core.Camera
import androidx.camera.core.CameraSelector
import androidx.camera.core.FocusMeteringAction
import androidx.camera.core.ImageCapture
import androidx.camera.core.ImageCaptureException
import androidx.camera.core.ImageProxy
import androidx.camera.core.Preview
import androidx.camera.core.UseCaseGroup
import androidx.camera.core.ViewPort
import androidx.camera.core.resolutionselector.AspectRatioStrategy
import androidx.camera.core.resolutionselector.ResolutionSelector
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.gestures.detectTransformGestures
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.LocalLifecycleOwner
import io.github.arnavdotexe.filmsim.look.Look
import io.github.arnavdotexe.filmsim.look.LookLibrary
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import java.util.concurrent.TimeUnit
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

private const val TAG = "FilmSim"

/** 4:3 sensor output (3:4 upright), the shape of the frame. */
private val FOUR_THREE = ResolutionSelector.Builder()
    .setAspectRatioStrategy(AspectRatioStrategy.RATIO_4_3_FALLBACK_AUTO_STRATEGY)
    .build()

/**
 * The camera screen, laid out after PhotonCamera: a top bar of toggles, the 3:4 viewfinder with
 * zoom pills and the manual panel over it, then the looks picker and the bottom bar.
 */
@Composable
fun CameraScreen() {
    val context = LocalContext.current
    val lifecycleOwner = LocalLifecycleOwner.current
    val scope = rememberCoroutineScope()

    var library by remember { mutableStateOf<LookLibrary?>(null) }
    var selected by rememberSaveable { mutableIntStateOf(0) } // 0: no look, n: library.looks[n - 1]
    var status by remember { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }
    var countdown by remember { mutableIntStateOf(0) }
    var flash by rememberSaveable { mutableStateOf(Flash.OFF) }
    var timer by rememberSaveable { mutableStateOf(Timer.OFF) }
    var grid by rememberSaveable { mutableStateOf(false) }
    var level by rememberSaveable { mutableStateOf(false) }
    var pro by rememberSaveable { mutableStateOf(false) }
    var back by rememberSaveable { mutableStateOf(true) }
    var camera by remember { mutableStateOf<Camera?>(null) }
    var zoom by remember { mutableFloatStateOf(1f) }
    var zoomRange by remember { mutableStateOf(1f to 1f) }
    var evIndex by remember { mutableIntStateOf(0) }
    var manual by remember { mutableStateOf(Manual()) }
    var ranges by remember { mutableStateOf<ManualRanges?>(null) }
    var live by remember { mutableStateOf(LiveExposure()) }
    var setting by remember { mutableStateOf(ManualSetting.EV) }
    var focusAt by remember { mutableStateOf<Offset?>(null) }
    var focusTap by remember { mutableIntStateOf(0) }
    var angle by remember { mutableFloatStateOf(0f) }
    var lastPhoto by remember { mutableStateOf<Uri?>(null) }
    var thumb by remember { mutableStateOf<Bitmap?>(null) }

    val processor = remember { FilmProcessor() }
    val capture = remember {
        ImageCapture.Builder()
            .setCaptureMode(ImageCapture.CAPTURE_MODE_MAXIMIZE_QUALITY)
            .setResolutionSelector(FOUR_THREE)
            .build()
    }
    val previewView = remember {
        PreviewView(context).apply {
            implementationMode = PreviewView.ImplementationMode.COMPATIBLE
            scaleType = PreviewView.ScaleType.FILL_CENTER
        }
    }

    LaunchedEffect(Unit) {
        library = runCatching { withContext(Dispatchers.IO) { loadBundledLooks(context) } }
            .onFailure { Log.e(TAG, "bundled looks failed to load", it); status = "Looks failed to load" }
            .getOrNull()
        lastPhoto = latestPhoto(context)
        thumb = lastPhoto?.let { thumbnail(context, it) }
    }
    val looks = library?.looks.orEmpty()
    val look: Look? = looks.getOrNull(selected - 1)
    LaunchedEffect(look) { processor.setLook(look?.lut) }
    LaunchedEffect(flash) { capture.flashMode = when (flash) {
        Flash.OFF -> ImageCapture.FLASH_MODE_OFF
        Flash.AUTO -> ImageCapture.FLASH_MODE_AUTO
        Flash.ON -> ImageCapture.FLASH_MODE_ON
    } }
    LaunchedEffect(camera, manual) { camera?.let { ManualCamera.apply(it, manual, live) } }

    // Photos come out upright however the phone is held.
    DisposableEffect(Unit) {
        val listener = object : OrientationEventListener(context) {
            override fun onOrientationChanged(degrees: Int) {
                if (degrees == ORIENTATION_UNKNOWN) return
                capture.targetRotation = when (degrees) {
                    in 45 until 135 -> Surface.ROTATION_270
                    in 135 until 225 -> Surface.ROTATION_180
                    in 225 until 315 -> Surface.ROTATION_90
                    else -> Surface.ROTATION_0
                }
            }
        }
        listener.enable()
        onDispose { listener.disable() }
    }

    // The horizon level, from gravity.
    DisposableEffect(level) {
        val sensors = context.getSystemService(Context.SENSOR_SERVICE) as SensorManager
        val gravity = sensors.getDefaultSensor(Sensor.TYPE_GRAVITY) ?: sensors.getDefaultSensor(Sensor.TYPE_ACCELEROMETER)
        val listener = object : SensorEventListener {
            override fun onSensorChanged(event: SensorEvent) {
                angle = CameraControls.levelAngle(event.values[0], event.values[1])
            }
            override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) = Unit
        }
        if (level && gravity != null) sensors.registerListener(listener, gravity, SensorManager.SENSOR_DELAY_UI)
        onDispose { sensors.unregisterListener(listener) }
    }

    DisposableEffect(lifecycleOwner, back) {
        val future = ProcessCameraProvider.getInstance(context)
        future.addListener({
            val provider = future.get()
            val previewBuilder = Preview.Builder().setResolutionSelector(FOUR_THREE)
            ManualCamera.watchExposure(previewBuilder) { live = it }
            val preview = previewBuilder.build().also { it.surfaceProvider = previewView.surfaceProvider }
            // One 3:4 frame for preview and capture: the photo is exactly what the preview shows.
            val group = UseCaseGroup.Builder()
                .setViewPort(ViewPort.Builder(Rational(3, 4), Surface.ROTATION_0).setScaleType(ViewPort.FILL_CENTER).build())
                .addUseCase(preview)
                .addUseCase(capture)
                .addEffect(FilmEffect(processor))
                .build()
            val selector = if (back) CameraSelector.DEFAULT_BACK_CAMERA else CameraSelector.DEFAULT_FRONT_CAMERA
            provider.unbindAll()
            val bound = runCatching { provider.bindToLifecycle(lifecycleOwner, selector, group) }
                .onFailure { Log.e(TAG, "camera bind failed", it); status = "Camera unavailable" }
                .getOrNull() ?: return@addListener
            camera = bound
            ranges = ManualCamera.ranges(bound)
            manual = Manual()
            evIndex = 0
            bound.cameraInfo.zoomState.observe(lifecycleOwner) { z ->
                zoom = z.zoomRatio
                zoomRange = z.minZoomRatio to z.maxZoomRatio
            }
        }, ContextCompat.getMainExecutor(context))
        onDispose { runCatching { future.get().unbindAll() } }
    }
    DisposableEffect(Unit) { onDispose { processor.release() } }

    fun takePhoto() {
        busy = true
        scope.launch {
            for (s in timer.seconds downTo 1) {
                countdown = s
                delay(1000)
            }
            countdown = 0
            status = "Developing..."
            status = try {
                val image = capture.takePicture(context)
                val bitmap = try { image.uprightBitmap() } finally { image.close() }
                renderLook(bitmap, look)
                val uri = saveToGallery(context, bitmap, look?.name)
                bitmap.recycle()
                lastPhoto = uri
                thumb = thumbnail(context, uri)
                null
            } catch (e: Exception) {
                Log.e(TAG, "capture failed", e)
                "Capture failed: ${e.message}"
            }
            busy = false
        }
    }

    Column(Modifier.fillMaxSize().statusBarsPadding()) {
        TopBar(
            flash, timer, grid, level, pro, flashAvailable = camera?.cameraInfo?.hasFlashUnit() == true,
            onFlash = { flash = flash.next() }, onTimer = { timer = timer.next() },
            onGrid = { grid = !grid }, onLevel = { level = !level }, onPro = { pro = !pro },
        )
        Box(Modifier.fillMaxWidth().aspectRatio(3f / 4f)) {
            AndroidView({ previewView }, Modifier.fillMaxSize())
            Box(
                Modifier.fillMaxSize()
                    .pointerInput(camera) {
                        detectTapGestures { at ->
                            val cam = camera ?: return@detectTapGestures
                            val point = previewView.meteringPointFactory.createPoint(at.x, at.y)
                            cam.cameraControl.startFocusAndMetering(
                                FocusMeteringAction.Builder(point).setAutoCancelDuration(4, TimeUnit.SECONDS).build(),
                            )
                            focusAt = at
                            focusTap++
                            if (manual.focus != null) manual = manual.copy(focus = null)
                        }
                    }
                    .pointerInput(camera) {
                        detectTransformGestures { _, _, scale, _ ->
                            val cam = camera ?: return@detectTransformGestures
                            cam.cameraControl.setZoomRatio((zoom * scale).coerceIn(zoomRange.first, zoomRange.second))
                        }
                    },
            ) {
                if (grid) GridOverlay()
                if (level) LevelOverlay(angle)
                focusAt?.let { FocusRing(it, focusTap) }
                if (pro && live.iso > 0) {
                    Text(
                        "ISO ${manual.iso ?: live.iso}   ${CameraControls.shutterLabel(manual.shutterNs ?: live.shutterNs)}",
                        color = Color.White, fontSize = 12.sp, fontWeight = FontWeight.SemiBold,
                        modifier = Modifier.align(Alignment.TopStart).padding(12.dp),
                    )
                }
                if (countdown > 0) {
                    Text("$countdown", color = Color.White, fontSize = 96.sp, fontWeight = FontWeight.Light, modifier = Modifier.align(Alignment.Center))
                }
                Column(Modifier.align(Alignment.BottomCenter), horizontalAlignment = Alignment.CenterHorizontally) {
                    ZoomPills(
                        CameraControls.zoomStops(zoomRange.first, zoomRange.second), zoom,
                        onZoom = { camera?.cameraControl?.setZoomRatio(it.coerceIn(zoomRange.first, zoomRange.second)) },
                        modifier = Modifier.padding(bottom = 8.dp),
                    )
                    if (pro) ProPanel(camera, ranges, manual, { manual = it }, live, evIndex, { evIndex = it }, setting, { setting = it })
                }
            }
        }
        Column(Modifier.fillMaxWidth().weight(1f).navigationBarsPadding(), horizontalAlignment = Alignment.CenterHorizontally) {
            Spacer(Modifier.weight(1f))
            Text(
                status ?: look?.description ?: "No look: the camera's own colour.",
                color = Color(0xFFBBBBBB), fontSize = 12.sp, maxLines = 2,
                modifier = Modifier.padding(horizontal = 20.dp),
            )
            LookPicker(listOf("None") + looks.map { it.name }, selected) { selected = it; status = null }
            Spacer(Modifier.weight(1f))
            BottomBar(
                thumb, onGallery = { lastPhoto?.let { openPhoto(context, it) } },
                busy = busy, onShutter = ::takePhoto, onFlip = { back = !back }, canFlip = !busy,
            )
        }
    }
}

/** The manual panel wired to the camera: EV, ISO, shutter, focus and white balance. */
@Composable
private fun ProPanel(
    camera: Camera?, ranges: ManualRanges?, manual: Manual, onManual: (Manual) -> Unit, live: LiveExposure,
    evIndex: Int, onEv: (Int) -> Unit, setting: ManualSetting, onSetting: (ManualSetting) -> Unit,
) {
    val exposure = camera?.cameraInfo?.exposureState
    val evRange = exposure?.exposureCompensationRange
    val evStep = exposure?.exposureCompensationStep?.toFloat() ?: 0f
    val r = ranges ?: ManualRanges(false, 100, 100, 0L, 0L, 0f)
    val isoStops = CameraControls.isoStops(r.isoMin, r.isoMax)
    val shutterStops = CameraControls.shutterStops(r.shutterMinNs, r.shutterMaxNs)
    val focusStops = if (r.minFocus > 0f) (0..20).map { r.minFocus * it / 20f } else emptyList()
    val manualExposure = manual.iso != null || manual.shutterNs != null
    ManualPanel(
        setting, onSetting,
        evLabel = if (manualExposure) "-" else CameraControls.evLabel(evIndex, evStep), evManual = evIndex != 0,
        isoLabel = "${manual.iso ?: live.iso}", isoManual = manual.iso != null,
        shutterLabel = if (live.shutterNs > 0 || manual.shutterNs != null) CameraControls.shutterLabel(manual.shutterNs ?: live.shutterNs) else "-",
        shutterManual = manual.shutterNs != null,
        focusLabel = manual.focus?.let { if (it <= 0f) "INF" else "%.2fm".format(1f / it) } ?: "AF", focusManual = manual.focus != null,
        wbLabel = manual.whiteBalance.label, wbManual = manual.whiteBalance != WhiteBalance.AUTO,
        manualSensor = r.manualSensor,
    ) {
        when (setting) {
            ManualSetting.EV -> if (evRange != null && evRange.lower < evRange.upper) {
                val values = (evRange.lower..evRange.upper).toList()
                StopDial(values.map { CameraControls.evLabel(it, evStep) }, values.indexOf(evIndex).takeIf { evIndex != 0 }) { i ->
                    val index = i?.let { values[it] } ?: 0
                    onEv(index)
                    camera.cameraControl.setExposureCompensationIndex(index)
                }
            }
            ManualSetting.ISO -> StopDial(isoStops.map { "$it" }, manual.iso?.let { isoStops.indexOf(it) }) { i ->
                onManual(manual.copy(iso = i?.let { isoStops[it] }))
            }
            ManualSetting.SHUTTER -> StopDial(shutterStops.map(CameraControls::shutterLabel), manual.shutterNs?.let { shutterStops.indexOf(it) }) { i ->
                onManual(manual.copy(shutterNs = i?.let { shutterStops[it] }))
            }
            ManualSetting.FOCUS -> StopDial(focusStops.map { "$it" }, manual.focus?.let { focusStops.indexOf(it) }) { i ->
                onManual(manual.copy(focus = i?.let { focusStops[it] }))
            }
            ManualSetting.WB -> {
                val presets = WhiteBalance.entries.drop(1)
                StopDial(presets.map { it.label }, presets.indexOf(manual.whiteBalance).takeIf { it >= 0 }) { i ->
                    onManual(manual.copy(whiteBalance = i?.let { presets[it] } ?: WhiteBalance.AUTO))
                }
            }
        }
    }
}

private suspend fun ImageCapture.takePicture(context: Context): ImageProxy = suspendCancellableCoroutine { cont ->
    takePicture(ContextCompat.getMainExecutor(context), object : ImageCapture.OnImageCapturedCallback() {
        override fun onCaptureSuccess(image: ImageProxy) = cont.resume(image)
        override fun onError(exception: ImageCaptureException) = cont.resumeWithException(exception)
    })
}
