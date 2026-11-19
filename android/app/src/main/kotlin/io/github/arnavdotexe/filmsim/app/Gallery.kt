package io.github.arnavdotexe.filmsim.app

import android.content.ContentUris
import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.net.Uri
import android.provider.MediaStore
import android.util.Size
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/** The newest photo Emulsion saved (DCIM/Emulsion), or null. Needs no permission: the app owns it. */
suspend fun latestPhoto(context: Context): Uri? = withContext(Dispatchers.IO) {
    val columns = arrayOf(MediaStore.Images.Media._ID)
    context.contentResolver.query(
        MediaStore.Images.Media.EXTERNAL_CONTENT_URI, columns,
        "${MediaStore.Images.Media.RELATIVE_PATH} LIKE ?", arrayOf("DCIM/Emulsion%"),
        "${MediaStore.Images.Media.DATE_ADDED} DESC",
    )?.use { cursor ->
        if (cursor.moveToFirst()) ContentUris.withAppendedId(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, cursor.getLong(0)) else null
    }
}

/** A small thumbnail of [uri] for the gallery button. */
suspend fun thumbnail(context: Context, uri: Uri): Bitmap? = withContext(Dispatchers.IO) {
    runCatching { context.contentResolver.loadThumbnail(uri, Size(192, 192), null) }.getOrNull()
}

/** Open [uri] in the phone's photo viewer. */
fun openPhoto(context: Context, uri: Uri) {
    val intent = Intent(Intent.ACTION_VIEW).setDataAndType(uri, "image/jpeg")
        .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
    runCatching { context.startActivity(intent) }
}
