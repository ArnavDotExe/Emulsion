package io.github.arnavdotexe.filmsim.app

import android.content.ContentValues
import android.content.Context
import android.graphics.Bitmap
import android.graphics.Matrix
import android.net.Uri
import android.provider.MediaStore
import androidx.camera.core.ImageProxy
import io.github.arnavdotexe.filmsim.look.Look
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.withContext
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** The upright frame of a capture, as a mutable ARGB bitmap. */
fun ImageProxy.uprightBitmap(): Bitmap {
    val frame = toBitmap()
    val degrees = imageInfo.rotationDegrees
    if (degrees == 0) return frame.copy(Bitmap.Config.ARGB_8888, true)
    val turned = Bitmap.createBitmap(frame, 0, 0, frame.width, frame.height, Matrix().apply { postRotate(degrees.toFloat()) }, true)
    return if (turned.isMutable) turned else turned.copy(Bitmap.Config.ARGB_8888, true)
}

/** Render [look] onto [bitmap] in place at full resolution, on all cores. */
suspend fun renderLook(bitmap: Bitmap, look: Look?) = withContext(Dispatchers.Default) {
    if (look == null) return@withContext
    val pixels = IntArray(bitmap.width * bitmap.height)
    bitmap.getPixels(pixels, 0, bitmap.width, 0, 0, bitmap.width, bitmap.height)
    val chunks = Runtime.getRuntime().availableProcessors().coerceAtLeast(1) * 4
    val step = (pixels.size + chunks - 1) / chunks
    coroutineScope {
        (0 until chunks).map { c ->
            async { look.lut.applyArgb(pixels, minOf(c * step, pixels.size), minOf((c + 1) * step, pixels.size)) }
        }.awaitAll()
    }
    bitmap.setPixels(pixels, 0, bitmap.width, 0, 0, bitmap.width, bitmap.height)
}

/** Save [bitmap] as a JPEG in DCIM/Emulsion and return its content URI. */
suspend fun saveToGallery(context: Context, bitmap: Bitmap, lookName: String?): Uri = withContext(Dispatchers.IO) {
    val stamp = SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US).format(Date())
    val values = ContentValues().apply {
        put(MediaStore.Images.Media.DISPLAY_NAME, "EMULSION_$stamp.jpg")
        put(MediaStore.Images.Media.MIME_TYPE, "image/jpeg")
        put(MediaStore.Images.Media.RELATIVE_PATH, "DCIM/Emulsion")
        put(MediaStore.Images.Media.IS_PENDING, 1)
        if (lookName != null) put(MediaStore.Images.Media.DESCRIPTION, "Look: $lookName")
    }
    val resolver = context.contentResolver
    val uri = checkNotNull(resolver.insert(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, values)) { "MediaStore refused the photo" }
    try {
        resolver.openOutputStream(uri).use { out ->
            check(out != null && bitmap.compress(Bitmap.CompressFormat.JPEG, 95, out)) { "could not write the photo" }
        }
        resolver.update(uri, ContentValues().apply { put(MediaStore.Images.Media.IS_PENDING, 0) }, null, null)
    } catch (e: Exception) {
        resolver.delete(uri, null, null)
        throw e
    }
    uri
}
