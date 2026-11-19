package io.github.arnavdotexe.filmsim.app

import kotlin.test.Test
import kotlin.test.assertEquals

/** The camera controls' pure logic: stops, labels, the level and the setting cycles. */
class CameraControlsTest {
    @Test
    fun zoomStopsCoverTheLensesInRange() {
        assertEquals(listOf(0.5f, 1f, 2f, 5f), CameraControls.zoomStops(0.5f, 8f))
        assertEquals(listOf(0.6f, 1f, 2f, 5f, 10f), CameraControls.zoomStops(0.62f, 30f))
        assertEquals(listOf(1f, 2f), CameraControls.zoomStops(1f, 4f))
        assertEquals(listOf(1f), CameraControls.zoomStops(1f, 1f))
    }

    @Test
    fun isoStopsAreFullStopsWithinTheSensorRange() {
        assertEquals(listOf(50, 100, 200, 400, 800, 1600, 3200), CameraControls.isoStops(43, 3500))
        assertEquals(listOf(100, 200, 400), CameraControls.isoStops(100, 400))
    }

    @Test
    fun shutterStopsAreTheClassicSpeedsWithinTheSensorRange() {
        val stops = CameraControls.shutterStops(minNs = 100_000L, maxNs = 1_000_000_000L)
        assertEquals(
            listOf("1/8000", "1/4000", "1/2000", "1/1000", "1/500", "1/250", "1/125", "1/60", "1/30", "1/15",
                "1/8", "1/4", "1/2", "1s"),
            stops.map(CameraControls::shutterLabel),
        )
        assertEquals(listOf("1/60", "1/30"), CameraControls.shutterStops(10_000_000L, 40_000_000L).map(CameraControls::shutterLabel))
    }

    @Test
    fun shutterLabelsReadLikeACameraDial() {
        assertEquals("1/125", CameraControls.shutterLabel(8_000_000L))
        assertEquals("1/60", CameraControls.shutterLabel(16_666_667L))
        assertEquals("1/4", CameraControls.shutterLabel(250_000_000L))
        assertEquals("1s", CameraControls.shutterLabel(1_000_000_000L))
        assertEquals("2.5s", CameraControls.shutterLabel(2_500_000_000L))
    }

    @Test
    fun evLabelsShowSignedStops() {
        assertEquals("0", CameraControls.evLabel(0, 1f / 6))
        assertEquals("+1.0", CameraControls.evLabel(6, 1f / 6))
        assertEquals("-0.7", CameraControls.evLabel(-2, 1f / 3))
        assertEquals("+0.3", CameraControls.evLabel(1, 1f / 3))
    }

    @Test
    fun levelAngleIsTheRollFromGravityAndSnapsWhenLevel() {
        assertEquals(0f, CameraControls.levelAngle(0f, 9.81f))
        assertEquals(90f, CameraControls.levelAngle(9.81f, 0f), 0.01f)
        assertEquals(-45f, CameraControls.levelAngle(-6.937f, 6.937f), 0.01f)
        assertEquals(true, CameraControls.isLevel(0.6f))
        assertEquals(false, CameraControls.isLevel(1.5f))
    }

    @Test
    fun flashAndTimerCycleThroughTheirSettings() {
        assertEquals(Flash.AUTO, Flash.OFF.next())
        assertEquals(Flash.ON, Flash.AUTO.next())
        assertEquals(Flash.OFF, Flash.ON.next())
        assertEquals(Timer.THREE, Timer.OFF.next())
        assertEquals(Timer.TEN, Timer.THREE.next())
        assertEquals(Timer.OFF, Timer.TEN.next())
        assertEquals(listOf(0, 3, 10), Timer.entries.map { it.seconds })
    }
}
