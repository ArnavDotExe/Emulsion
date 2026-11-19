package io.github.arnavdotexe.filmsim.app

import java.io.File
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

/** The demo images the films tab previews each film on. */
class DemoScenesTest {
    private val assets = File("src/main/assets")

    @Test
    fun threeScenesCoverLandscapePortraitAndStillLife() {
        assertEquals(listOf("Landscape", "Portrait", "Still life"), DemoScene.entries.map { it.title })
    }

    @Test
    fun everySceneHasABundledImageOfTheFramesShape() {
        for (scene in DemoScene.entries) {
            val file = File(assets, scene.assetPath)
            assertTrue(file.isFile, "${scene.assetPath} is bundled")
            val png = file.readBytes()
            assertEquals(0x89, png[0].toInt() and 0xFF, "a PNG")
            val width = ((png[16].toInt() and 0xFF) shl 24) or ((png[17].toInt() and 0xFF) shl 16) or ((png[18].toInt() and 0xFF) shl 8) or (png[19].toInt() and 0xFF)
            val height = ((png[20].toInt() and 0xFF) shl 24) or ((png[21].toInt() and 0xFF) shl 16) or ((png[22].toInt() and 0xFF) shl 8) or (png[23].toInt() and 0xFF)
            assertEquals(3 * height, 4 * width, "${scene.assetPath} is 3:4 upright")
            assertTrue(png.size < 700_000, "${scene.assetPath} stays small (${png.size} bytes)")
        }
    }
}
