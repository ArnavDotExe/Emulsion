package io.github.arnavdotexe.filmsim.app

import android.hardware.camera2.CameraCaptureSession
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraMetadata
import android.hardware.camera2.CaptureRequest
import android.hardware.camera2.CaptureResult
import android.hardware.camera2.TotalCaptureResult
import android.hardware.camera2.params.ColorSpaceTransform
import android.hardware.camera2.params.RggbChannelVector
import androidx.annotation.OptIn
import androidx.camera.camera2.interop.Camera2CameraControl
import androidx.camera.camera2.interop.Camera2CameraInfo
import androidx.camera.camera2.interop.Camera2Interop
import androidx.camera.camera2.interop.CaptureRequestOptions
import androidx.camera.camera2.interop.ExperimentalCamera2Interop
import androidx.camera.core.Camera
import androidx.camera.core.ExtendableBuilder

/** White balance presets for the manual panel. */
enum class WhiteBalance(val label: String, val mode: Int) {
    AUTO("AUTO", CameraMetadata.CONTROL_AWB_MODE_AUTO),
    DAYLIGHT("DAY", CameraMetadata.CONTROL_AWB_MODE_DAYLIGHT),
    CLOUDY("CLOUD", CameraMetadata.CONTROL_AWB_MODE_CLOUDY_DAYLIGHT),
    SHADE("SHADE", CameraMetadata.CONTROL_AWB_MODE_SHADE),
    TUNGSTEN("TUNG", CameraMetadata.CONTROL_AWB_MODE_INCANDESCENT),
    FLUORESCENT("FLUO", CameraMetadata.CONTROL_AWB_MODE_FLUORESCENT),
}

/** The manual settings; null means automatic. Focus is in diopters (0 = infinity). */
data class Manual(
    val iso: Int? = null,
    val shutterNs: Long? = null,
    val focus: Float? = null,
    val whiteBalance: WhiteBalance = WhiteBalance.AUTO,
) {
    val isAuto get() = iso == null && shutterNs == null && focus == null && whiteBalance == WhiteBalance.AUTO
}

/** What the current camera allows manually. */
data class ManualRanges(
    val manualSensor: Boolean,
    val isoMin: Int, val isoMax: Int,
    val shutterMinNs: Long, val shutterMaxNs: Long,
    val minFocus: Float,
)

/**
 * The latest exposure, read from the preview's capture results, and the white balance (gains and
 * colour matrix) last chosen automatically. Manual exposure holds that white balance: with
 * auto-exposure off, the Pixel's automatic white balance drifts strongly cyan.
 */
data class LiveExposure(
    val iso: Int = 0,
    val shutterNs: Long = 0L,
    val wbGains: RggbChannelVector? = null,
    val wbTransform: ColorSpaceTransform? = null,
)

object ManualCamera {
    /**
     * Turn electronic stabilisation off. On the Pixel it crops about 12% off the field of view
     * (measured against the stock app, whose 1x is that much wider), so photos looked zoomed in.
     */
    @OptIn(markerClass = [ExperimentalCamera2Interop::class])
    fun <T> fullFieldOfView(builder: ExtendableBuilder<T>) {
        Camera2Interop.Extender(builder).setCaptureRequestOption(
            CaptureRequest.CONTROL_VIDEO_STABILIZATION_MODE, CameraMetadata.CONTROL_VIDEO_STABILIZATION_MODE_OFF,
        )
    }

    /** Report each preview frame's ISO and shutter time to [onExposure] (the HUD and the manual dials start there). */
    @OptIn(markerClass = [ExperimentalCamera2Interop::class])
    fun <T> watchExposure(builder: ExtendableBuilder<T>, onExposure: (LiveExposure) -> Unit) {
        Camera2Interop.Extender(builder).setSessionCaptureCallback(object : CameraCaptureSession.CaptureCallback() {
            private var last = 0L
            private var gains: RggbChannelVector? = null
            private var transform: ColorSpaceTransform? = null

            override fun onCaptureCompleted(session: CameraCaptureSession, request: CaptureRequest, result: TotalCaptureResult) {
                val now = System.nanoTime()
                if (now - last < 200_000_000L) return
                last = now
                val iso = result.get(CaptureResult.SENSOR_SENSITIVITY) ?: return
                val time = result.get(CaptureResult.SENSOR_EXPOSURE_TIME) ?: return
                val autoWb = result.get(CaptureResult.CONTROL_AE_MODE) != CameraMetadata.CONTROL_AE_MODE_OFF &&
                    result.get(CaptureResult.CONTROL_AWB_MODE) == CameraMetadata.CONTROL_AWB_MODE_AUTO
                if (autoWb) {
                    gains = result.get(CaptureResult.COLOR_CORRECTION_GAINS) ?: gains
                    transform = result.get(CaptureResult.COLOR_CORRECTION_TRANSFORM) ?: transform
                }
                onExposure(LiveExposure(iso, time, gains, transform))
            }
        })
    }

    @OptIn(markerClass = [ExperimentalCamera2Interop::class])
    fun ranges(camera: Camera): ManualRanges {
        val info = Camera2CameraInfo.from(camera.cameraInfo)
        val caps = info.getCameraCharacteristic(CameraCharacteristics.REQUEST_AVAILABLE_CAPABILITIES) ?: IntArray(0)
        val iso = info.getCameraCharacteristic(CameraCharacteristics.SENSOR_INFO_SENSITIVITY_RANGE)
        val time = info.getCameraCharacteristic(CameraCharacteristics.SENSOR_INFO_EXPOSURE_TIME_RANGE)
        val focus = info.getCameraCharacteristic(CameraCharacteristics.LENS_INFO_MINIMUM_FOCUS_DISTANCE) ?: 0f
        val manual = CameraMetadata.REQUEST_AVAILABLE_CAPABILITIES_MANUAL_SENSOR in caps && iso != null && time != null
        return ManualRanges(
            manualSensor = manual,
            isoMin = iso?.lower ?: 100, isoMax = iso?.upper ?: 100,
            shutterMinNs = time?.lower ?: 0L, shutterMaxNs = minOf(time?.upper ?: 0L, 30_000_000_000L),
            minFocus = focus,
        )
    }

    /** Apply [manual] to every request of [camera]; the automatic part of a manual exposure keeps [live]'s value. */
    @OptIn(markerClass = [ExperimentalCamera2Interop::class])
    fun apply(camera: Camera, manual: Manual, live: LiveExposure) {
        val options = CaptureRequestOptions.Builder()
        options.setCaptureRequestOption(CaptureRequest.CONTROL_VIDEO_STABILIZATION_MODE, CameraMetadata.CONTROL_VIDEO_STABILIZATION_MODE_OFF)
        if (manual.iso != null || manual.shutterNs != null) {
            options.setCaptureRequestOption(CaptureRequest.CONTROL_AE_MODE, CameraMetadata.CONTROL_AE_MODE_OFF)
            options.setCaptureRequestOption(CaptureRequest.SENSOR_SENSITIVITY, manual.iso ?: live.iso.coerceAtLeast(50))
            options.setCaptureRequestOption(CaptureRequest.SENSOR_EXPOSURE_TIME, manual.shutterNs ?: live.shutterNs.coerceAtLeast(1_000_000L))
        }
        if (manual.focus != null) {
            options.setCaptureRequestOption(CaptureRequest.CONTROL_AF_MODE, CameraMetadata.CONTROL_AF_MODE_OFF)
            options.setCaptureRequestOption(CaptureRequest.LENS_FOCUS_DISTANCE, manual.focus)
        }
        if (manual.whiteBalance != WhiteBalance.AUTO) {
            options.setCaptureRequestOption(CaptureRequest.CONTROL_AWB_MODE, manual.whiteBalance.mode)
        } else if ((manual.iso != null || manual.shutterNs != null) && live.wbGains != null && live.wbTransform != null) {
            options.setCaptureRequestOption(CaptureRequest.CONTROL_AWB_MODE, CameraMetadata.CONTROL_AWB_MODE_OFF)
            options.setCaptureRequestOption(CaptureRequest.COLOR_CORRECTION_MODE, CameraMetadata.COLOR_CORRECTION_MODE_TRANSFORM_MATRIX)
            options.setCaptureRequestOption(CaptureRequest.COLOR_CORRECTION_GAINS, live.wbGains)
            options.setCaptureRequestOption(CaptureRequest.COLOR_CORRECTION_TRANSFORM, live.wbTransform)
        }
        Camera2CameraControl.from(camera.cameraControl).setCaptureRequestOptions(options.build())
    }
}
