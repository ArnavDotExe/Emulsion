package io.github.arnavdotexe.filmsim.app

import java.util.Locale
import kotlin.math.abs
import kotlin.math.atan2
import kotlin.math.roundToInt
import kotlin.math.roundToLong

/** Flash for captures, cycled from the top bar. */
enum class Flash(val label: String) {
    OFF("OFF"), AUTO("AUTO"), ON("ON");

    fun next(): Flash = entries[(ordinal + 1) % entries.size]
}

/** The self-timer, cycled from the top bar. */
enum class Timer(val seconds: Int) {
    OFF(0), THREE(3), TEN(10);

    fun next(): Timer = entries[(ordinal + 1) % entries.size]
}

/** The pure logic behind the camera controls: stops for the dials and pills, and their labels. */
object CameraControls {
    private val LENS_STOPS = listOf(1f, 2f, 5f, 10f)
    private val ISO_STOPS = generateSequence(25) { it * 2 }.takeWhile { it <= 409_600 }.toList()

    /** The classic shutter speeds, 1/8000 s to 30 s, in nanoseconds. */
    private val SHUTTER_STOPS: List<Long> =
        listOf(8000, 4000, 2000, 1000, 500, 250, 125, 60, 30, 15, 8, 4, 2).map { (1e9 / it).roundToLong() } +
            listOf(1L, 2L, 4L, 8L, 15L, 30L).map { it * 1_000_000_000L }

    /** Zoom pills for a camera whose zoom runs [min]..[max]: its widest lens (if wider than 1x), then 1x, 2x, 5x, 10x. */
    fun zoomStops(min: Float, max: Float): List<Float> {
        val wide = if (min < 0.99f) listOf((min * 10).roundToInt() / 10f) else emptyList()
        return wide + LENS_STOPS.filter { it <= max + 1e-3f }
    }

    /** Full ISO stops (50, 100, 200, ...) inside the sensor's range. */
    fun isoStops(min: Int, max: Int): List<Int> = ISO_STOPS.filter { it in min..max }

    /** Classic shutter speeds inside the sensor's exposure-time range, shortest first. */
    fun shutterStops(minNs: Long, maxNs: Long): List<Long> = SHUTTER_STOPS.filter { it in minNs..maxNs }

    /** A shutter time as a camera dial writes it: 1/125, 1/4, 1s, 2.5s. */
    fun shutterLabel(ns: Long): String {
        if (ns >= 999_000_000L) {
            val s = ns / 1e9
            return if (abs(s - s.roundToLong()) < 0.05) "${s.roundToLong()}s" else String.format(Locale.US, "%.1fs", s)
        }
        return "1/${(1e9 / ns).roundToLong()}"
    }

    /** Exposure compensation as signed stops: 0, +1.0, -0.7. */
    fun evLabel(index: Int, step: Float): String =
        if (index == 0) "0" else (if (index > 0) "+" else "-") + String.format(Locale.US, "%.1f", abs(index * step))

    /** The phone's roll in degrees from gravity along the screen's x and y axes (0 when upright). */
    fun levelAngle(x: Float, y: Float): Float = Math.toDegrees(atan2(x.toDouble(), y.toDouble())).toFloat()

    /** Within a degree of level. */
    fun isLevel(angle: Float): Boolean = abs(angle) < 1f
}
