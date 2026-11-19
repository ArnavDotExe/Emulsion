package io.github.arnavdotexe.filmsim.app

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.GridItemSpan
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.graphics.scale
import io.github.arnavdotexe.filmsim.look.Look
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/** A demo scene's bundled image, at thumbnail size. */
private fun loadScene(context: Context, scene: DemoScene): Bitmap? = runCatching {
    context.assets.open(scene.assetPath).use { BitmapFactory.decodeStream(it) }?.let { full ->
        full.scale(THUMB_WIDTH, THUMB_WIDTH * full.height / full.width).also { if (it !== full) full.recycle() }
    }
}.getOrNull()

private const val THUMB_WIDTH = 240

/** [scene] rendered through [look]'s LUT (the same lookup as captures). */
private fun renderThumb(scene: Bitmap, look: Look?): Bitmap {
    val w = scene.width
    val h = scene.height
    val pixels = IntArray(w * h)
    scene.getPixels(pixels, 0, w, 0, 0, w, h)
    look?.lut?.applyArgb(pixels)
    return Bitmap.createBitmap(pixels, w, h, Bitmap.Config.ARGB_8888)
}

/**
 * The films tab: every film as a card, previewed on a demo scene (landscape, portrait or still
 * life) and grouped by house. Tapping a card loads that film and returns to the camera.
 */
@Composable
fun FilmsTab(
    films: List<Film>,
    looksByName: Map<String, Look>,
    selected: String?,
    onSelect: (Film?) -> Unit,
    onTab: (Tab) -> Unit,
) {
    val context = LocalContext.current
    var house by rememberSaveable { mutableStateOf<House?>(null) }
    var scene by rememberSaveable { mutableStateOf(DemoScene.LANDSCAPE) }
    val sceneBitmap by produceState<Bitmap?>(null, scene) { value = withContext(Dispatchers.IO) { loadScene(context, scene) } }
    // Thumbnails already rendered for this scene, so scrolling back doesn't redo them.
    val cache = remember(scene) { HashMap<String, Bitmap>() }
    val current = selected?.let { FilmCatalog.filmOf(films, it) }
    val shown = films.filter { house == null || it.house == house }

    Column(
        Modifier.fillMaxSize().background(Color(0xFF0B0B0B)).statusBarsPadding()
            // Touches stop here: they must not reach the camera under this tab.
            .pointerInput(Unit) { detectTapGestures { } },
    ) {
        Row(Modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
            Box(
                Modifier.size(44.dp).clip(CircleShape).clickable { onTab(Tab.PHOTO) },
                contentAlignment = Alignment.Center,
            ) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back to the camera", tint = Color.White) }
            Column(Modifier.weight(1f).padding(start = 4.dp)) {
                Text("Films", color = Color.White, fontSize = 22.sp, fontWeight = FontWeight.Bold)
                Text(
                    current?.let { "Loaded: ${it.name}" } ?: "No film loaded: the camera's own colour",
                    color = Muted, fontSize = 12.sp, maxLines = 1, overflow = TextOverflow.Ellipsis,
                )
            }
        }
        Row(Modifier.padding(start = 16.dp, top = 2.dp), verticalAlignment = Alignment.CenterVertically) {
            Text("PREVIEW ON", color = Muted, fontSize = 10.sp, letterSpacing = 1.5.sp, fontWeight = FontWeight.SemiBold)
        }
        LazyRow(contentPadding = PaddingValues(horizontal = 12.dp, vertical = 6.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            items(DemoScene.entries) { s -> Chip(s.title, scene == s) { scene = s } }
        }
        LazyRow(contentPadding = PaddingValues(horizontal = 12.dp, vertical = 2.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            item { Chip("All", house == null) { house = null } }
            items(House.entries.filter { h -> films.any { it.house == h } }) { h -> Chip(h.title, house == h) { house = h } }
        }
        LazyVerticalGrid(
            columns = GridCells.Fixed(3),
            modifier = Modifier.weight(1f),
            contentPadding = PaddingValues(12.dp),
            horizontalArrangement = Arrangement.spacedBy(10.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp),
        ) {
            if (house == null) {
                item(key = "none") { FilmCard("None", "The camera's own colour", sceneBitmap, null, cache, selected == null) { onSelect(null) } }
            }
            var lastHouse: House? = null
            for (film in shown) {
                if (film.house != lastHouse) {
                    lastHouse = film.house
                    item(key = "header-${film.house}", span = { GridItemSpan(maxLineSpan) }) {
                        Column(Modifier.padding(top = 8.dp)) {
                            Text(
                                film.house.title.uppercase(), color = Color.White, fontSize = 12.sp, letterSpacing = 1.5.sp,
                                fontWeight = FontWeight.Bold,
                            )
                            Text(film.house.tagline, color = Muted, fontSize = 11.sp)
                        }
                    }
                }
                item(key = film.lookName) {
                    FilmCard(film.name, film.hint, sceneBitmap, looksByName[film.lookName], cache, film == current) { onSelect(film) }
                }
            }
        }
        ModeSwitcher(Tab.FILMS, onTab, Modifier.navigationBarsPadding())
    }
}

@Composable
private fun Chip(label: String, on: Boolean, onClick: () -> Unit) {
    Text(
        label, color = if (on) Color.Black else Color.White, fontSize = 12.sp, fontWeight = FontWeight.SemiBold,
        modifier = Modifier.clip(CircleShape).background(if (on) Amber else ChipFill).clickable(onClick = onClick)
            .padding(horizontal = 14.dp, vertical = 8.dp),
    )
}

@Composable
private fun FilmCard(
    name: String, hint: String, scene: Bitmap?, look: Look?, cache: HashMap<String, Bitmap>, selected: Boolean, onClick: () -> Unit,
) {
    val shape = RoundedCornerShape(14.dp)
    // Rendered only while the card is on screen (a lazy grid composes just the visible cards).
    val thumb by produceState<Bitmap?>(null, scene, look) {
        value = scene?.let { s -> cache[name] ?: withContext(Dispatchers.Default) { renderThumb(s, look) }.also { cache[name] = it } }
    }
    Column(Modifier.clickable(onClick = onClick).semantics { contentDescription = "Film $name" }) {
        Box(
            Modifier.fillMaxWidth().aspectRatio(3f / 4f).clip(shape).background(Color(0xFF1C1C1C))
                .border(if (selected) 2.dp else 0.dp, if (selected) Amber else Color.Transparent, shape),
        ) {
            thumb?.let { Image(it.asImageBitmap(), null, Modifier.fillMaxSize(), contentScale = ContentScale.Crop) }
        }
        Text(
            name, color = if (selected) Amber else Color.White, fontSize = 13.sp, fontWeight = FontWeight.SemiBold,
            maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.padding(top = 6.dp),
        )
        Text(hint, color = Muted, fontSize = 10.sp, lineHeight = 13.sp, maxLines = 2, overflow = TextOverflow.Ellipsis)
    }
}
