package io.github.arnavdotexe.filmsim.app

import android.graphics.Bitmap
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CameraRoll
import androidx.compose.material.icons.filled.Cameraswitch
import androidx.compose.material.icons.filled.CenterFocusStrong
import androidx.compose.material.icons.filled.Exposure
import androidx.compose.material.icons.filled.FlashAuto
import androidx.compose.material.icons.filled.FlashOff
import androidx.compose.material.icons.filled.FlashOn
import androidx.compose.material.icons.filled.GridOff
import androidx.compose.material.icons.filled.GridOn
import androidx.compose.material.icons.filled.Straighten
import androidx.compose.material.icons.filled.Timer10
import androidx.compose.material.icons.filled.Timer3
import androidx.compose.material.icons.filled.TimerOff
import androidx.compose.material.icons.filled.WbSunny
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.Slider
import androidx.compose.material3.SliderDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import java.util.Locale
import kotlin.math.abs
import kotlin.math.roundToInt

val Amber = Color(0xFFF5A83B)
val LevelGreen = Color(0xFF7FC98A)
val Scrim = Color(0x99000000)
val Muted = Color(0xFF9A9A9A)
val ChipFill = Color(0x1FFFFFFF)

/** A round icon button for the top bar; amber when the setting is on. */
@Composable
private fun TopIcon(icon: ImageVector, description: String, active: Boolean, enabled: Boolean = true, onClick: () -> Unit) {
    Box(
        Modifier.size(44.dp).clip(CircleShape).clickable(enabled = enabled, onClick = onClick),
        contentAlignment = Alignment.Center,
    ) {
        Icon(
            icon, description,
            tint = when {
                !enabled -> Color(0x55FFFFFF)
                active -> Amber
                else -> Color.White
            },
            modifier = Modifier.size(24.dp),
        )
    }
}

/** The top bar: flash, self-timer, grid and level, each cycled or toggled in place. */
@Composable
fun TopBar(
    flash: Flash, timer: Timer, grid: Boolean, level: Boolean, flashAvailable: Boolean,
    onFlash: () -> Unit, onTimer: () -> Unit, onGrid: () -> Unit, onLevel: () -> Unit,
) {
    Row(
        Modifier.fillMaxWidth().height(52.dp).padding(horizontal = 20.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        val flashIcon = when (flash) {
            Flash.OFF -> Icons.Filled.FlashOff
            Flash.AUTO -> Icons.Filled.FlashAuto
            Flash.ON -> Icons.Filled.FlashOn
        }
        TopIcon(flashIcon, "Flash ${flash.label.lowercase()}", flash != Flash.OFF, flashAvailable, onFlash)
        val timerIcon = when (timer) {
            Timer.OFF -> Icons.Filled.TimerOff
            Timer.THREE -> Icons.Filled.Timer3
            Timer.TEN -> Icons.Filled.Timer10
        }
        TopIcon(timerIcon, "Self-timer", timer != Timer.OFF, onClick = onTimer)
        TopIcon(if (grid) Icons.Filled.GridOn else Icons.Filled.GridOff, "Grid", grid, onClick = onGrid)
        TopIcon(Icons.Filled.Straighten, "Level", level, onClick = onLevel)
    }
}

/** Rule-of-thirds lines over the frame (never in the photo). */
@Composable
fun GridOverlay() {
    Canvas(Modifier.fillMaxSize()) {
        val c = Color(0x59FFFFFF)
        for (i in 1..2) {
            drawLine(c, Offset(size.width * i / 3, 0f), Offset(size.width * i / 3, size.height), 1.dp.toPx())
            drawLine(c, Offset(0f, size.height * i / 3), Offset(size.width, size.height * i / 3), 1.dp.toPx())
        }
    }
}

/** A horizon line that stays level with the world, with its tilt; green once the phone is level. */
@Composable
fun LevelOverlay(angle: Float) {
    val level = CameraControls.isLevel(angle)
    val color = if (level) LevelGreen else Color.White
    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        Canvas(Modifier.size(width = 180.dp, height = 2.dp).rotate(if (level) 0f else angle)) {
            drawLine(color, Offset(0f, 0f), Offset(size.width * 0.38f, 0f), 2.dp.toPx())
            drawLine(color, Offset(size.width * 0.62f, 0f), Offset(size.width, 0f), 2.dp.toPx())
        }
        Text(if (level) "0°" else "${abs(angle).roundToInt()}°", color = color, fontSize = 11.sp, fontWeight = FontWeight.SemiBold)
    }
}

/** The focus reticle where the user tapped. */
@Composable
fun FocusRing(at: Offset) {
    val r = with(LocalDensity.current) { 36.dp.toPx() }
    Canvas(Modifier.offset { IntOffset((at.x - r).roundToInt(), (at.y - r).roundToInt()) }.size(72.dp)) {
        drawCircle(Amber, radius = size.minDimension / 2 - 2.dp.toPx(), style = Stroke(1.5.dp.toPx()))
        drawCircle(Amber, radius = 2.dp.toPx())
    }
}

/** Zoom chips for the lenses in range; the active one shows the exact zoom. */
@Composable
fun ZoomChips(stops: List<Float>, zoom: Float, onZoom: (Float) -> Unit, modifier: Modifier = Modifier) {
    if (stops.size < 2) return
    Row(modifier.clip(CircleShape).background(Scrim).padding(4.dp), horizontalArrangement = Arrangement.spacedBy(2.dp)) {
        val active = stops.lastOrNull { zoom >= it - 0.05f } ?: stops.first()
        for (stop in stops) {
            val on = stop == active
            val text = if (on) String.format(Locale.US, "%.1fx", zoom).replace(".0x", "x") else zoomLabel(stop)
            Box(
                Modifier.size(38.dp).clip(CircleShape).background(if (on) Color(0x33FFFFFF) else Color.Transparent)
                    .clickable { onZoom(stop) }.semantics { contentDescription = "Zoom ${zoomLabel(stop)}" },
                contentAlignment = Alignment.Center,
            ) { Text(text, color = if (on) Amber else Color.White, fontSize = if (on) 12.sp else 11.sp, fontWeight = FontWeight.SemiBold) }
        }
    }
}

private fun zoomLabel(stop: Float) = if (stop % 1f == 0f) "${stop.toInt()}x" else "${stop}x".replace("0.", ".")

/** The current film over the viewfinder, like the label in a camera's film window. */
@Composable
fun FilmBadge(name: String?, onClick: () -> Unit, modifier: Modifier = Modifier) {
    Row(
        modifier.clip(CircleShape).background(Scrim).clickable(onClick = onClick).padding(horizontal = 10.dp, vertical = 5.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(Icons.Filled.CameraRoll, null, tint = if (name != null) Amber else Color.White, modifier = Modifier.size(14.dp))
        Spacer(Modifier.width(6.dp))
        Text((name ?: "No film").uppercase(), color = Color.White, fontSize = 11.sp, fontWeight = FontWeight.SemiBold, letterSpacing = 1.sp)
    }
}

/** The quick-control panels; one is open at a time. */
enum class QuickPanel { NONE, EXPOSURE, FOCUS, LIGHT }

/** A quick-control chip: an icon over a short uppercase status. */
@Composable
private fun QuickChip(icon: ImageVector, title: String, status: String, open: Boolean, set: Boolean, onClick: () -> Unit, modifier: Modifier) {
    Column(
        modifier.clip(RoundedCornerShape(14.dp)).background(if (open) Color(0x33FFFFFF) else Color.Transparent)
            .clickable(onClick = onClick).padding(vertical = 6.dp).semantics { contentDescription = title },
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Icon(icon, null, tint = if (open || set) Amber else Color.White, modifier = Modifier.size(22.dp))
        Text(
            status.uppercase(), color = if (set) Amber else Muted, fontSize = 10.sp, fontWeight = FontWeight.SemiBold,
            letterSpacing = 0.5.sp, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.padding(horizontal = 4.dp),
        )
    }
}

/** The quick controls under the viewfinder: exposure, focus, light, and the film (which opens the films tab). */
@Composable
fun QuickChips(
    open: QuickPanel, onOpen: (QuickPanel) -> Unit,
    exposure: String, exposureSet: Boolean, focus: String, focusSet: Boolean, light: String, lightSet: Boolean,
    film: String?, onFilms: () -> Unit,
) {
    Row(Modifier.fillMaxWidth().padding(horizontal = 12.dp), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        val cell = Modifier.weight(1f)
        fun toggle(p: QuickPanel) = onOpen(if (open == p) QuickPanel.NONE else p)
        QuickChip(Icons.Filled.Exposure, "Exposure", exposure, open == QuickPanel.EXPOSURE, exposureSet, { toggle(QuickPanel.EXPOSURE) }, cell)
        QuickChip(Icons.Filled.CenterFocusStrong, "Focus", focus, open == QuickPanel.FOCUS, focusSet, { toggle(QuickPanel.FOCUS) }, cell)
        QuickChip(Icons.Filled.WbSunny, "Light", light, open == QuickPanel.LIGHT, lightSet, { toggle(QuickPanel.LIGHT) }, cell)
        QuickChip(Icons.Filled.CameraRoll, "Film", film ?: "None", false, film != null, onFilms, cell)
    }
}

/** A panel over the bottom of the viewfinder, on a dark scrim. */
@Composable
fun PanelSurface(title: String, modifier: Modifier = Modifier, content: @Composable () -> Unit) {
    Column(
        modifier.fillMaxWidth().clip(RoundedCornerShape(topStart = 14.dp, topEnd = 14.dp)).background(Color(0xCC000000))
            .padding(top = 10.dp, bottom = 8.dp),
    ) {
        Text(title.uppercase(), color = Muted, fontSize = 10.sp, letterSpacing = 1.5.sp, modifier = Modifier.padding(horizontal = 16.dp))
        content()
    }
}

/** One dial: a name, its value, a slider over the stops and AUTO; [index] null means automatic (the slider rests at [autoSlider]). */
@Composable
fun DialRow(name: String, labels: List<String>, index: Int?, onIndex: (Int?) -> Unit, autoValue: String? = null, autoSlider: Int = 0) {
    Row(Modifier.fillMaxWidth().padding(start = 16.dp, end = 12.dp, top = 4.dp), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.width(64.dp)) {
            Text(name, color = Muted, fontSize = 10.sp, letterSpacing = 1.sp)
            Text(
                index?.let { labels.getOrNull(it) } ?: autoValue ?: "AUTO",
                color = if (index != null) Amber else Color.White, fontSize = 14.sp, fontWeight = FontWeight.SemiBold, maxLines = 1,
            )
        }
        if (labels.size >= 2) {
            Slider(
                value = (index ?: autoSlider.coerceIn(0, labels.lastIndex)).toFloat(),
                onValueChange = { onIndex(it.roundToInt().coerceIn(0, labels.lastIndex)) },
                valueRange = 0f..labels.lastIndex.toFloat(),
                steps = (labels.size - 2).coerceAtLeast(0),
                colors = SliderDefaults.colors(
                    thumbColor = Color.White, activeTrackColor = if (index != null) Amber else Color(0x55FFFFFF),
                    inactiveTrackColor = Color(0x33FFFFFF), activeTickColor = Color.Transparent, inactiveTickColor = Color.Transparent,
                ),
                modifier = Modifier.weight(1f).padding(horizontal = 8.dp),
            )
        } else {
            Spacer(Modifier.weight(1f))
        }
        Text(
            "AUTO", color = if (index == null) Color.Black else Color.White, fontSize = 11.sp, fontWeight = FontWeight.SemiBold,
            modifier = Modifier.clip(CircleShape).background(if (index == null) Color.White else ChipFill)
                .clickable { onIndex(null) }.padding(horizontal = 10.dp, vertical = 5.dp),
        )
    }
}

/** A row of choices, one selected (the white balance presets). */
@Composable
fun ChoiceRow(choices: List<String>, selected: Int, onSelect: (Int) -> Unit) {
    LazyRow(Modifier.fillMaxWidth().padding(top = 8.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        item { Spacer(Modifier.width(8.dp)) }
        items(choices.size) { i ->
            val on = i == selected
            Text(
                choices[i], color = if (on) Color.Black else Color.White, fontSize = 12.sp, fontWeight = FontWeight.SemiBold,
                modifier = Modifier.clip(CircleShape).background(if (on) Amber else ChipFill)
                    .clickable { onSelect(i) }.padding(horizontal = 14.dp, vertical = 8.dp),
            )
        }
        item { Spacer(Modifier.width(8.dp)) }
    }
}

enum class Tab(val label: String) { PHOTO("PHOTO"), FILMS("FILMS") }

/** The tabs, written like a camera's mode switcher: the photo camera and the films. */
@Composable
fun ModeSwitcher(tab: Tab, onTab: (Tab) -> Unit, modifier: Modifier = Modifier) {
    Row(modifier.fillMaxWidth().padding(vertical = 6.dp), horizontalArrangement = Arrangement.Center) {
        for (t in Tab.entries) {
            val on = t == tab
            Column(
                Modifier.clip(RoundedCornerShape(10.dp)).clickable { onTab(t) }.padding(horizontal = 18.dp, vertical = 4.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                Text(t.label, color = if (on) Amber else Color(0xFFBBBBBB), fontSize = 13.sp, fontWeight = FontWeight.Bold, letterSpacing = 1.5.sp)
                Box(Modifier.padding(top = 3.dp).size(4.dp).clip(CircleShape).background(if (on) Amber else Color.Transparent))
            }
        }
    }
}

/** The bottom bar: the last photo, the shutter (with a ring while a photo develops) and the camera flip. */
@Composable
fun BottomBar(thumb: Bitmap?, onGallery: () -> Unit, busy: Boolean, onShutter: () -> Unit, onFlip: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().padding(horizontal = 32.dp, vertical = 6.dp),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(
            Modifier.size(56.dp).clip(RoundedCornerShape(14.dp)).background(ChipFill).border(1.5.dp, Color(0x66FFFFFF), RoundedCornerShape(14.dp))
                .clickable(enabled = thumb != null, onClick = onGallery).semantics { contentDescription = "Last photo" },
        ) {
            if (thumb != null) Image(thumb.asImageBitmap(), null, Modifier.fillMaxSize(), contentScale = ContentScale.Crop)
        }
        Box(Modifier.size(84.dp), contentAlignment = Alignment.Center) {
            Box(
                Modifier.size(78.dp).border(4.dp, Color.White, CircleShape).padding(8.dp).clip(CircleShape)
                    .background(if (busy) Color(0xFF777777) else Color.White)
                    .clickable(enabled = !busy, onClick = onShutter).semantics { contentDescription = "Take photo" },
            )
            if (busy) CircularProgressIndicator(Modifier.size(84.dp), color = Amber, strokeWidth = 3.dp)
        }
        Box(
            Modifier.size(56.dp).clip(CircleShape).background(ChipFill).clickable(enabled = !busy, onClick = onFlip),
            contentAlignment = Alignment.Center,
        ) { Icon(Icons.Filled.Cameraswitch, "Switch camera", tint = Color.White, modifier = Modifier.size(26.dp)) }
    }
}
