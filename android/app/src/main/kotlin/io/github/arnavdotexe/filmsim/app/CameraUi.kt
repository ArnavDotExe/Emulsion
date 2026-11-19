package io.github.arnavdotexe.filmsim.app

import android.graphics.Bitmap
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Slider
import androidx.compose.material3.SliderDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlin.math.roundToInt

val Cream = Color(0xFFF2E6C9)
val Amber = Color(0xFFE0A030)
private val Scrim = Color(0x99000000)

/** A small labelled toggle, like the markings on a camera's top plate. */
@Composable
fun Pill(text: String, active: Boolean, onClick: () -> Unit, modifier: Modifier = Modifier) {
    Text(
        text,
        color = if (active) Color.Black else Color.White,
        fontSize = 12.sp,
        fontWeight = FontWeight.SemiBold,
        letterSpacing = 1.sp,
        modifier = modifier
            .clip(RoundedCornerShape(14.dp))
            .background(if (active) Cream else Color(0x26FFFFFF))
            .clickable(onClick = onClick)
            .padding(horizontal = 10.dp, vertical = 6.dp),
    )
}

/** The top bar: flash, timer, grid, level and the manual (PRO) panel. */
@Composable
fun TopBar(
    flash: Flash, timer: Timer, grid: Boolean, level: Boolean, pro: Boolean, flashAvailable: Boolean,
    onFlash: () -> Unit, onTimer: () -> Unit, onGrid: () -> Unit, onLevel: () -> Unit, onPro: () -> Unit,
) {
    Row(
        Modifier.fillMaxWidth().height(48.dp).padding(horizontal = 12.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Pill(if (flashAvailable) "FLASH ${flash.label}" else "FLASH -", flashAvailable && flash != Flash.OFF, onFlash)
        Pill(if (timer == Timer.OFF) "TIMER OFF" else "TIMER ${timer.seconds}S", timer != Timer.OFF, onTimer)
        Pill("GRID", grid, onGrid)
        Pill("LEVEL", level, onLevel)
        Pill("PRO", pro, onPro)
    }
}

/** Rule-of-thirds lines over the frame. */
@Composable
fun GridOverlay() {
    Canvas(Modifier.fillMaxSize()) {
        val c = Color(0x66FFFFFF)
        for (i in 1..2) {
            drawLine(c, Offset(size.width * i / 3, 0f), Offset(size.width * i / 3, size.height), 1.dp.toPx())
            drawLine(c, Offset(0f, size.height * i / 3), Offset(size.width, size.height * i / 3), 1.dp.toPx())
        }
    }
}

/** A horizon line that stays level with the world; it turns amber when the phone is level. */
@Composable
fun LevelOverlay(angle: Float) {
    val level = CameraControls.isLevel(angle)
    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        Canvas(Modifier.size(width = 160.dp, height = 2.dp).rotate(if (level) 0f else angle)) {
            drawLine(if (level) Amber else Color.White, Offset(0f, 0f), Offset(size.width, 0f), 2.dp.toPx())
        }
    }
}

/** The focus ring where the user tapped. */
@Composable
fun FocusRing(at: Offset, key: Int) {
    val density = LocalDensity.current
    val scale by animateFloatAsState(if (key % 2 == 0) 1f else 1.0001f, label = "focus")
    val r = with(density) { 36.dp.toPx() }
    Canvas(Modifier.offset { IntOffset((at.x - r).roundToInt(), (at.y - r).roundToInt()) }.size(72.dp)) {
        drawCircle(Amber, radius = size.minDimension / 2 * scale - 2.dp.toPx(), style = Stroke(1.5.dp.toPx()))
    }
}

/** Zoom pills: the lenses in range; the current zoom is highlighted. */
@Composable
fun ZoomPills(stops: List<Float>, zoom: Float, onZoom: (Float) -> Unit, modifier: Modifier = Modifier) {
    Row(modifier.clip(RoundedCornerShape(20.dp)).background(Scrim).padding(4.dp), horizontalArrangement = Arrangement.spacedBy(4.dp)) {
        val active = stops.lastOrNull { zoom >= it - 0.05f } ?: stops.firstOrNull()
        for (stop in stops) {
            val on = stop == active
            val text = if (on && kotlin.math.abs(zoom - stop) > 0.05f) "%.1fx".format(zoom) else zoomLabel(stop)
            Box(
                Modifier.size(40.dp).clip(CircleShape).background(if (on) Color(0x33FFFFFF) else Color.Transparent)
                    .clickable { onZoom(stop) },
                contentAlignment = Alignment.Center,
            ) { Text(text, color = if (on) Amber else Color.White, fontSize = 12.sp, fontWeight = FontWeight.SemiBold) }
        }
    }
}

private fun zoomLabel(stop: Float) = if (stop % 1f == 0f) "${stop.toInt()}x" else "${stop}x".replace("0.", ".")

/** One cell of the manual panel: the setting's name and its value; the selected cell opens its dial. */
@Composable
private fun ManualCell(name: String, value: String, manual: Boolean, selected: Boolean, onClick: () -> Unit, modifier: Modifier) {
    Column(
        modifier.clip(RoundedCornerShape(10.dp)).background(if (selected) Color(0x33FFFFFF) else Color.Transparent)
            .clickable(onClick = onClick).padding(vertical = 6.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text(name, color = Color(0xFFAAAAAA), fontSize = 10.sp, letterSpacing = 1.sp)
        Text(value, color = if (manual) Amber else Color.White, fontSize = 14.sp, fontWeight = FontWeight.SemiBold)
    }
}

/** A dial over a list of stops with AUTO at its left end; [index] null means automatic. */
@Composable
fun StopDial(labels: List<String>, index: Int?, onIndex: (Int?) -> Unit) {
    Row(Modifier.fillMaxWidth().padding(horizontal = 12.dp), verticalAlignment = Alignment.CenterVertically) {
        Pill("AUTO", index == null, { onIndex(null) })
        if (labels.size >= 2) {
            Slider(
                value = (index ?: 0).toFloat(),
                onValueChange = { onIndex(it.roundToInt().coerceIn(0, labels.lastIndex)) },
                valueRange = 0f..labels.lastIndex.toFloat(),
                steps = (labels.size - 2).coerceAtLeast(0),
                colors = SliderDefaults.colors(thumbColor = Cream, activeTrackColor = Amber, inactiveTrackColor = Color(0x55FFFFFF),
                    activeTickColor = Color.Transparent, inactiveTickColor = Color.Transparent),
                modifier = Modifier.weight(1f).padding(horizontal = 12.dp),
            )
        }
    }
}

enum class ManualSetting { EV, ISO, SHUTTER, FOCUS, WB }

/** PhotonCamera-style manual panel: a row of cells (EV, ISO, shutter, focus, white balance) and the selected cell's dial. */
@Composable
fun ManualPanel(
    selected: ManualSetting, onSelect: (ManualSetting) -> Unit,
    evLabel: String, evManual: Boolean,
    isoLabel: String, isoManual: Boolean,
    shutterLabel: String, shutterManual: Boolean,
    focusLabel: String, focusManual: Boolean,
    wbLabel: String, wbManual: Boolean,
    manualSensor: Boolean,
    dial: @Composable () -> Unit,
) {
    Column(Modifier.fillMaxWidth().background(Scrim).padding(vertical = 6.dp)) {
        Row(Modifier.fillMaxWidth().padding(horizontal = 8.dp), horizontalArrangement = Arrangement.spacedBy(4.dp)) {
            val cell = Modifier.weight(1f)
            ManualCell("EV", evLabel, evManual, selected == ManualSetting.EV, { onSelect(ManualSetting.EV) }, cell)
            if (manualSensor) {
                ManualCell("ISO", isoLabel, isoManual, selected == ManualSetting.ISO, { onSelect(ManualSetting.ISO) }, cell)
                ManualCell("SHUTTER", shutterLabel, shutterManual, selected == ManualSetting.SHUTTER, { onSelect(ManualSetting.SHUTTER) }, cell)
            }
            ManualCell("FOCUS", focusLabel, focusManual, selected == ManualSetting.FOCUS, { onSelect(ManualSetting.FOCUS) }, cell)
            ManualCell("WB", wbLabel, wbManual, selected == ManualSetting.WB, { onSelect(ManualSetting.WB) }, cell)
        }
        dial()
    }
}

/** The looks, as a horizontal picker in place of PhotonCamera's mode switcher. */
@Composable
fun LookPicker(names: List<String>, selected: Int, onSelect: (Int) -> Unit) {
    val state = rememberLazyListState(initialFirstVisibleItemIndex = maxOf(0, selected - 1))
    LaunchedEffect(selected) { state.animateScrollToItem(maxOf(0, selected - 1)) }
    LazyRow(
        state = state,
        contentPadding = PaddingValues(horizontal = 16.dp, vertical = 6.dp),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        itemsIndexed(names) { index, name ->
            val on = index == selected
            Text(
                name.uppercase(),
                color = if (on) Amber else Color(0xFFBBBBBB),
                fontWeight = if (on) FontWeight.Bold else FontWeight.Medium,
                fontSize = 13.sp,
                letterSpacing = 1.sp,
                modifier = Modifier.clip(RoundedCornerShape(12.dp)).clickable { onSelect(index) }.padding(horizontal = 8.dp, vertical = 6.dp),
            )
        }
    }
}

/** The bottom bar: the last photo, the shutter (with a ring while a photo develops) and the camera flip. */
@Composable
fun BottomBar(thumb: Bitmap?, onGallery: () -> Unit, busy: Boolean, onShutter: () -> Unit, onFlip: () -> Unit, canFlip: Boolean) {
    Row(
        Modifier.fillMaxWidth().padding(horizontal = 28.dp, vertical = 8.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(
            Modifier.size(52.dp).clip(CircleShape).background(Color(0x33FFFFFF)).border(1.5.dp, Color(0x88FFFFFF), CircleShape)
                .clickable(enabled = thumb != null, onClick = onGallery).semantics { contentDescription = "Last photo" },
        ) {
            if (thumb != null) Image(thumb.asImageBitmap(), null, Modifier.fillMaxSize(), contentScale = ContentScale.Crop)
        }
        Box(Modifier.size(80.dp), contentAlignment = Alignment.Center) {
            Box(
                Modifier.size(76.dp).border(4.dp, Color.White, CircleShape).padding(8.dp).clip(CircleShape)
                    .background(if (busy) Color.Gray else Color.White)
                    .clickable(enabled = !busy, onClick = onShutter).semantics { contentDescription = "Take photo" },
            )
            if (busy) CircularProgressIndicator(Modifier.size(80.dp), color = Amber, strokeWidth = 3.dp)
        }
        Box(
            Modifier.size(52.dp).clip(CircleShape).background(Color(0x33FFFFFF)).clickable(enabled = canFlip, onClick = onFlip)
                .semantics { contentDescription = "Switch camera" },
            contentAlignment = Alignment.Center,
        ) {
            Canvas(Modifier.size(24.dp)) {
                val stroke = Stroke(2.dp.toPx())
                drawArc(Color.White, 200f, 140f, false, style = stroke)
                drawArc(Color.White, 20f, 140f, false, style = stroke)
            }
        }
    }
}
