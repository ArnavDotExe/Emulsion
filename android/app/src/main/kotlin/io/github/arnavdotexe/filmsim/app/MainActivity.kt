package io.github.arnavdotexe.filmsim.app

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.os.Bundle
import android.util.Log
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageCapture
import androidx.camera.core.ImageCaptureException
import androidx.camera.core.ImageProxy
import androidx.camera.core.Preview
import androidx.camera.core.UseCaseGroup
import androidx.camera.core.ViewPort
import androidx.camera.core.resolutionselector.AspectRatioStrategy
import androidx.camera.core.resolutionselector.ResolutionSelector
import android.util.Rational
import android.view.Surface
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.LocalLifecycleOwner
import io.github.arnavdotexe.filmsim.look.Look
import io.github.arnavdotexe.filmsim.look.LookLibrary
import io.github.arnavdotexe.filmsim.look.LookSource
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme(colorScheme = darkColorScheme()) { App() }
        }
    }
}

/** The bundled looks from assets/looks. */
fun loadBundledLooks(context: Context): LookLibrary {
    val assets = context.assets
    val files = assets.list("looks").orEmpty().filter { it.endsWith(".fsim.json") }
    return LookLibrary.load(files.map { name -> LookSource(name, assets.open("looks/$name").bufferedReader().use { it.readText() }) })
}

@Composable
private fun App() {
    val context = LocalContext.current
    var granted by remember {
        mutableStateOf(ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED)
    }
    val ask = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted = it }
    LaunchedEffect(Unit) { if (!granted) ask.launch(Manifest.permission.CAMERA) }
    Box(Modifier.fillMaxSize().background(Color.Black)) {
        if (granted) {
            CameraScreen()
        } else {
            Text(
                "Emulsion needs the camera to take photos.",
                color = Color.White,
                modifier = Modifier.align(Alignment.Center).clickable { ask.launch(Manifest.permission.CAMERA) }.padding(24.dp),
            )
        }
    }
}

@Composable
private fun CameraScreen() {
    val context = LocalContext.current
    val lifecycleOwner = LocalLifecycleOwner.current
    val scope = rememberCoroutineScope()
    var library by remember { mutableStateOf<LookLibrary?>(null) }
    var selected by rememberSaveable { mutableIntStateOf(0) } // 0: no look, n: library.looks[n - 1]
    var status by remember { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }
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
            .onFailure { Log.e("FilmSim", "bundled looks failed to load", it); status = "Looks failed to load" }
            .getOrNull()
    }
    val looks = library?.looks.orEmpty()
    val look: Look? = looks.getOrNull(selected - 1)
    LaunchedEffect(look) { processor.setLook(look?.lut) }

    DisposableEffect(lifecycleOwner) {
        val future = ProcessCameraProvider.getInstance(context)
        future.addListener({
            val provider = future.get()
            val preview = Preview.Builder().setResolutionSelector(FOUR_THREE).build()
                .also { it.surfaceProvider = previewView.surfaceProvider }
            // One 3:4 frame for preview and capture: the photo is exactly what the preview shows.
            val rotation = previewView.display?.rotation ?: Surface.ROTATION_0
            val group = UseCaseGroup.Builder()
                .setViewPort(ViewPort.Builder(Rational(3, 4), rotation).setScaleType(ViewPort.FILL_CENTER).build())
                .addUseCase(preview)
                .addUseCase(capture)
                .addEffect(FilmEffect(processor))
                .build()
            provider.unbindAll()
            provider.bindToLifecycle(lifecycleOwner, CameraSelector.DEFAULT_BACK_CAMERA, group)
        }, ContextCompat.getMainExecutor(context))
        onDispose {
            runCatching { future.get().unbindAll() }
            processor.release()
        }
    }

    Column(Modifier.fillMaxSize().statusBarsPadding()) {
        // The frame: 3:4, the same crop as the photo.
        AndroidView({ previewView }, Modifier.fillMaxWidth().aspectRatio(3f / 4f))
        Column(
            Modifier.fillMaxWidth().weight(1f).navigationBarsPadding().padding(bottom = 16.dp),
            verticalArrangement = Arrangement.SpaceEvenly,
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Text(
                status ?: look?.description ?: "No look",
                color = Color(0xFFCCCCCC), fontSize = 12.sp, modifier = Modifier.padding(top = 10.dp, start = 16.dp, end = 16.dp),
            )
            LookStrip(listOf("None") + looks.map { it.name }, selected) { selected = it; status = null }
            Spacer(Modifier.height(8.dp))
            Shutter(enabled = !busy) {
                busy = true
                status = "Developing..."
                scope.launch {
                    status = try {
                        val image = capture.takePicture(context)
                        val bitmap = try { image.uprightBitmap() } finally { image.close() }
                        renderLook(bitmap, look)
                        saveToGallery(context, bitmap, look?.name)
                        bitmap.recycle()
                        "Saved to DCIM/Emulsion"
                    } catch (e: Exception) {
                        Log.e("FilmSim", "capture failed", e)
                        "Capture failed: ${e.message}"
                    }
                    busy = false
                }
            }
        }
    }
}

@Composable
private fun LookStrip(names: List<String>, selected: Int, onSelect: (Int) -> Unit) {
    val state = rememberLazyListState(initialFirstVisibleItemIndex = maxOf(0, selected - 1))
    LazyRow(
        state = state,
        contentPadding = PaddingValues(horizontal = 16.dp, vertical = 10.dp),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        itemsIndexed(names) { index, name ->
            val on = index == selected
            Text(
                name,
                color = if (on) Color.Black else Color.White,
                fontWeight = if (on) FontWeight.SemiBold else FontWeight.Normal,
                fontSize = 14.sp,
                modifier = Modifier
                    .background(if (on) Color(0xFFF2E6C9) else Color(0x33FFFFFF), RoundedCornerShape(16.dp))
                    .clickable { onSelect(index) }
                    .padding(horizontal = 14.dp, vertical = 8.dp),
            )
        }
    }
}

@Composable
private fun Shutter(enabled: Boolean, onClick: () -> Unit) {
    Box(
        Modifier
            .size(72.dp)
            .border(4.dp, Color.White, CircleShape)
            .padding(8.dp)
            .background(if (enabled) Color.White else Color.Gray, CircleShape)
            .clickable(enabled = enabled, onClick = onClick)
            .semantics { contentDescription = "Take photo" },
    )
}

/** 4:3 sensor output (3:4 upright), the shape of the frame. */
private val FOUR_THREE = ResolutionSelector.Builder()
    .setAspectRatioStrategy(AspectRatioStrategy.RATIO_4_3_FALLBACK_AUTO_STRATEGY)
    .build()

private suspend fun ImageCapture.takePicture(context: Context): ImageProxy = suspendCancellableCoroutine { cont ->
    takePicture(ContextCompat.getMainExecutor(context), object : ImageCapture.OnImageCapturedCallback() {
        override fun onCaptureSuccess(image: ImageProxy) = cont.resume(image)
        override fun onError(exception: ImageCaptureException) = cont.resumeWithException(exception)
    })
}
