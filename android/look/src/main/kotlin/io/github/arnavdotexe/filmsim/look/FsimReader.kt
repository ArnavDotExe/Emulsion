package io.github.arnavdotexe.filmsim.look

import kotlinx.serialization.SerializationException
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.intOrNull
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.Base64

/**
 * The strict `.fsim.json` v1 reader, the Kotlin twin of colorlab's `fsim.loads`: anything the
 * Python reader rejects (unknown keys, other versions, out-of-range settings, bad LUT data, text
 * over 8 MiB) raises [FsimFormatException], so a newer file never renders wrongly in silence.
 */
object FsimReader {
    const val FORMAT = "fsim"
    const val VERSION = 1
    const val MAX_TEXT_BYTES = 8 * 1024 * 1024
    const val MAX_NAME = 80
    const val MAX_TEXT_FIELD = 2000
    val LUT_SIZES = 2..65
    private val TEXT_FIELDS = listOf("description", "author", "license")
    private val TOP_LEVEL = setOf("format", "version", "name", "color") + TEXT_FIELDS + Section.entries.map { it.key }

    fun parse(text: String): Look {
        if (text.toByteArray(Charsets.UTF_8).size > MAX_TEXT_BYTES) fail("look text is larger than $MAX_TEXT_BYTES bytes")
        val doc = try {
            Json.parseToJsonElement(text)
        } catch (e: SerializationException) {
            fail("not valid JSON: ${e.message}")
        } catch (e: IllegalArgumentException) {
            fail("not valid JSON: ${e.message}")
        }
        if (doc !is JsonObject) fail("a look must be a JSON object")
        val unknown = doc.keys - TOP_LEVEL
        if (unknown.isNotEmpty()) fail("unknown sections: ${unknown.sorted()}")
        val format = (doc["format"] as? JsonPrimitive)?.takeIf { it.isString }?.content
        if (format != FORMAT) fail("format must be '$FORMAT', got ${doc["format"]}")
        val version = number(doc["version"])
        if (version == null || version != VERSION.toDouble()) {
            fail("unsupported version ${doc["version"]} (this reader supports $VERSION)")
        }
        val name = text(doc, "name", required = true)
        if (name.isBlank() || name.length > MAX_NAME) fail("name must be 1-$MAX_NAME characters, got '$name'")
        val color = doc["color"] as? JsonObject
        if (color == null || color.keys != setOf("lut")) fail("color: expected an object with a 'lut'")
        val texts = TEXT_FIELDS.associateWith { key ->
            text(doc, key, required = false).also {
                if (it.length > MAX_TEXT_FIELD) fail("$key must be text of at most $MAX_TEXT_FIELD characters")
            }
        }
        val sections = Section.entries.associateWith { section(it, doc[it.key]) }
        return Look(
            name = name,
            lut = lut(color.getValue("lut")),
            local = sections.getValue(Section.LOCAL),
            vignette = sections.getValue(Section.VIGNETTE),
            grain = sections.getValue(Section.GRAIN),
            description = texts.getValue("description"),
            author = texts.getValue("author"),
            license = texts.getValue("license"),
        )
    }

    /** A JSON number (not a string, boolean or null) as a Double, or null. */
    private fun number(node: JsonElement?): Double? {
        val p = node as? JsonPrimitive ?: return null
        if (p.isString || p.booleanOrNull != null || p.content == "null") return null
        return p.doubleOrNull
    }

    private fun text(doc: JsonObject, key: String, required: Boolean): String {
        val node = doc[key] ?: if (required) fail("a look needs a $key") else return ""
        if (node !is JsonPrimitive || !node.isString) fail("$key must be text")
        return node.content
    }

    private fun section(section: Section, node: JsonElement?): Map<String, Double> {
        if (node == null) return emptyMap()
        if (node !is JsonObject) fail("${section.key}: expected an object")
        val out = linkedMapOf<String, Double>()
        for ((name, value) in node) {
            val spec = section.settings[name] ?: fail("${section.key}: unknown setting '$name'")
            val v = number(value)
            if (v == null || !v.isFinite()) fail("${section.key}.$name: expected a number, got $value")
            if (v < spec.min || v > spec.max) fail("${section.key}.$name must be in ${spec.min}..${spec.max}, got $v")
            if (spec.wholeNumber && v != Math.floor(v)) fail("${section.key}.$name must be a whole number, got $v")
            if (v != spec.default) out[name] = v
        }
        return out
    }

    private fun lut(node: JsonElement): Lut3D {
        if (node !is JsonObject || node.keys != setOf("size", "data")) {
            fail("color.lut: expected an object with 'size' and 'data'")
        }
        val sizeNode = node.getValue("size") as? JsonPrimitive
        // A whole number written as an integer, as Python's isinstance(size, int) requires.
        val size = sizeNode?.takeIf { !it.isString && it.content.all(Char::isDigit) }?.intOrNull
        if (size == null || size !in LUT_SIZES) {
            fail("color.lut.size must be a whole number ${LUT_SIZES.first}..${LUT_SIZES.last}, got ${node["size"]}")
        }
        val data = (node.getValue("data") as? JsonPrimitive)?.takeIf { it.isString }?.content
            ?: fail("color.lut.data: expected base64 text")
        val raw = try {
            Base64.getDecoder().decode(data)
        } catch (e: IllegalArgumentException) {
            fail("color.lut.data: not valid base64")
        }
        val expected = size * size * size * 3 * 2
        if (raw.size != expected) fail("color.lut.data: expected $expected bytes for size $size, got ${raw.size}")
        val shorts = ByteBuffer.wrap(raw).order(ByteOrder.LITTLE_ENDIAN).asShortBuffer()
        val values = FloatArray(size * size * size * 3) { (shorts.get(it).toInt() and 0xFFFF) / 65535f }
        return Lut3D(size, values)
    }

    private fun fail(message: String): Nothing = throw FsimFormatException(message)
}
