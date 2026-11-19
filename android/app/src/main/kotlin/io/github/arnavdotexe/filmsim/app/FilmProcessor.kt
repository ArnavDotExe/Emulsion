package io.github.arnavdotexe.filmsim.app

import android.graphics.SurfaceTexture
import android.opengl.EGL14
import android.opengl.EGLConfig
import android.opengl.EGLContext
import android.opengl.EGLDisplay
import android.opengl.EGLExt
import android.opengl.EGLSurface
import android.opengl.GLES11Ext
import android.opengl.GLES30
import android.os.Handler
import android.os.HandlerThread
import android.util.Log
import android.view.Surface
import androidx.camera.core.CameraEffect
import androidx.camera.core.SurfaceOutput
import androidx.camera.core.SurfaceProcessor
import androidx.camera.core.SurfaceRequest
import io.github.arnavdotexe.filmsim.look.Lut3D
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.concurrent.Executor

/** The preview effect: every preview frame goes through [FilmProcessor]. */
class FilmEffect(processor: FilmProcessor) :
    CameraEffect(PREVIEW, processor.executor, processor, { Log.e(TAG, "film effect failed", it) })

/**
 * Renders camera frames through the selected look's 3D LUT on the GPU (OpenGL ES 3) for the
 * preview. The LUT lookup is tetrahedral, like colorlab's `apply_lut` and [Lut3D.lookup], so the
 * preview matches the captured photo. All GL work runs on one thread.
 */
class FilmProcessor : SurfaceProcessor {
    private val thread = HandlerThread("film-gl").apply { start() }
    private val handler = Handler(thread.looper)
    val executor = Executor { handler.post(it) }

    private var display: EGLDisplay = EGL14.EGL_NO_DISPLAY
    private var context: EGLContext = EGL14.EGL_NO_CONTEXT
    private var config: EGLConfig? = null
    private var pbuffer: EGLSurface = EGL14.EGL_NO_SURFACE
    private var program = 0
    private var cameraTexture = 0
    private var lutTexture = 0
    private var lutSize = 0
    private var pendingLut: Lut3D? = null
    private var input: SurfaceTexture? = null
    private val outputs = linkedMapOf<SurfaceOutput, EGLSurface>()
    private val stMatrix = FloatArray(16)
    private val texMatrix = FloatArray(16)
    private val quad = ByteBuffer.allocateDirect(8 * 4).order(ByteOrder.nativeOrder()).asFloatBuffer()
        .put(floatArrayOf(-1f, -1f, 1f, -1f, -1f, 1f, 1f, 1f)).also { it.position(0) }

    init {
        handler.post { setUpGl() }
    }

    /** Use [lut] for the next frames (null: no look). */
    fun setLook(lut: Lut3D?) = handler.post {
        pendingLut = lut ?: Lut3D.identity(2)
    }

    override fun onInputSurface(request: SurfaceRequest) {
        val texture = SurfaceTexture(cameraTexture)
        texture.setDefaultBufferSize(request.resolution.width, request.resolution.height)
        texture.setOnFrameAvailableListener({ draw(it) }, handler)
        val surface = Surface(texture)
        input = texture
        request.provideSurface(surface, executor) {
            texture.setOnFrameAvailableListener(null)
            surface.release()
            texture.release()
            if (input === texture) input = null
        }
    }

    override fun onOutputSurface(output: SurfaceOutput) {
        val surface = output.getSurface(executor) { event ->
            if (event.eventCode == SurfaceOutput.Event.EVENT_REQUEST_CLOSE) {
                outputs.remove(output)?.let { EGL14.eglDestroySurface(display, it) }
                output.close()
            }
        }
        val egl = EGL14.eglCreateWindowSurface(display, config, surface, intArrayOf(EGL14.EGL_NONE), 0)
        outputs[output] = egl
    }

    /** Stop rendering and free the GL resources. */
    fun release() = handler.post {
        for ((output, egl) in outputs) {
            EGL14.eglDestroySurface(display, egl)
            output.close()
        }
        outputs.clear()
        if (display != EGL14.EGL_NO_DISPLAY) {
            EGL14.eglMakeCurrent(display, EGL14.EGL_NO_SURFACE, EGL14.EGL_NO_SURFACE, EGL14.EGL_NO_CONTEXT)
            EGL14.eglDestroySurface(display, pbuffer)
            EGL14.eglDestroyContext(display, context)
            EGL14.eglTerminate(display)
        }
        thread.quitSafely()
    }

    private fun draw(texture: SurfaceTexture) {
        if (texture !== input) return
        EGL14.eglMakeCurrent(display, pbuffer, pbuffer, context)
        texture.updateTexImage()
        texture.getTransformMatrix(stMatrix)
        pendingLut?.let { upload(it) }
        pendingLut = null
        for ((output, egl) in outputs) {
            EGL14.eglMakeCurrent(display, egl, egl, context)
            output.updateTransformMatrix(texMatrix, stMatrix)
            GLES30.glViewport(0, 0, output.size.width, output.size.height)
            GLES30.glUseProgram(program)
            GLES30.glActiveTexture(GLES30.GL_TEXTURE0)
            GLES30.glBindTexture(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, cameraTexture)
            GLES30.glActiveTexture(GLES30.GL_TEXTURE1)
            GLES30.glBindTexture(GLES30.GL_TEXTURE_3D, lutTexture)
            GLES30.glUniformMatrix4fv(GLES30.glGetUniformLocation(program, "uTex"), 1, false, texMatrix, 0)
            GLES30.glUniform1f(GLES30.glGetUniformLocation(program, "uLutSize"), lutSize.toFloat())
            GLES30.glDrawArrays(GLES30.GL_TRIANGLE_STRIP, 0, 4)
            EGLExt.eglPresentationTimeANDROID(display, egl, texture.timestamp)
            EGL14.eglSwapBuffers(display, egl)
        }
    }

    private fun upload(lut: Lut3D) {
        val values = lut.values()
        val buffer = ByteBuffer.allocateDirect(values.size * 4).order(ByteOrder.nativeOrder()).asFloatBuffer()
        buffer.put(values).position(0)
        GLES30.glBindTexture(GLES30.GL_TEXTURE_3D, lutTexture)
        GLES30.glPixelStorei(GLES30.GL_UNPACK_ALIGNMENT, 1)
        GLES30.glTexImage3D(GLES30.GL_TEXTURE_3D, 0, GLES30.GL_RGB16F, lut.size, lut.size, lut.size, 0,
            GLES30.GL_RGB, GLES30.GL_FLOAT, buffer)
        lutSize = lut.size
    }

    private fun setUpGl() {
        display = EGL14.eglGetDisplay(EGL14.EGL_DEFAULT_DISPLAY)
        val version = IntArray(2)
        check(EGL14.eglInitialize(display, version, 0, version, 1)) { "eglInitialize failed" }
        val attribs = intArrayOf(
            EGL14.EGL_RED_SIZE, 8, EGL14.EGL_GREEN_SIZE, 8, EGL14.EGL_BLUE_SIZE, 8, EGL14.EGL_ALPHA_SIZE, 8,
            EGL14.EGL_RENDERABLE_TYPE, EGLExt.EGL_OPENGL_ES3_BIT_KHR,
            EGL14.EGL_SURFACE_TYPE, EGL14.EGL_WINDOW_BIT or EGL14.EGL_PBUFFER_BIT,
            EGL_RECORDABLE_ANDROID, 1, EGL14.EGL_NONE,
        )
        val configs = arrayOfNulls<EGLConfig>(1)
        val count = IntArray(1)
        check(EGL14.eglChooseConfig(display, attribs, 0, configs, 0, 1, count, 0) && count[0] > 0) { "no EGL config" }
        config = configs[0]
        context = EGL14.eglCreateContext(display, config, EGL14.EGL_NO_CONTEXT,
            intArrayOf(EGL14.EGL_CONTEXT_CLIENT_VERSION, 3, EGL14.EGL_NONE), 0)
        pbuffer = EGL14.eglCreatePbufferSurface(display, config,
            intArrayOf(EGL14.EGL_WIDTH, 1, EGL14.EGL_HEIGHT, 1, EGL14.EGL_NONE), 0)
        EGL14.eglMakeCurrent(display, pbuffer, pbuffer, context)

        program = link(compile(GLES30.GL_VERTEX_SHADER, VERTEX), compile(GLES30.GL_FRAGMENT_SHADER, FRAGMENT))
        GLES30.glUseProgram(program)
        GLES30.glUniform1i(GLES30.glGetUniformLocation(program, "uCamera"), 0)
        GLES30.glUniform1i(GLES30.glGetUniformLocation(program, "uLut"), 1)
        val position = GLES30.glGetAttribLocation(program, "aPos")
        GLES30.glEnableVertexAttribArray(position)
        GLES30.glVertexAttribPointer(position, 2, GLES30.GL_FLOAT, false, 0, quad)

        val textures = IntArray(2)
        GLES30.glGenTextures(2, textures, 0)
        cameraTexture = textures[0]
        lutTexture = textures[1]
        GLES30.glBindTexture(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, cameraTexture)
        GLES30.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, GLES30.GL_TEXTURE_MIN_FILTER, GLES30.GL_LINEAR)
        GLES30.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, GLES30.GL_TEXTURE_MAG_FILTER, GLES30.GL_LINEAR)
        GLES30.glBindTexture(GLES30.GL_TEXTURE_3D, lutTexture)
        for (p in intArrayOf(GLES30.GL_TEXTURE_MIN_FILTER, GLES30.GL_TEXTURE_MAG_FILTER)) {
            GLES30.glTexParameteri(GLES30.GL_TEXTURE_3D, p, GLES30.GL_NEAREST)
        }
        upload(pendingLut ?: Lut3D.identity(2))
        pendingLut = null
    }

    private fun compile(type: Int, source: String): Int {
        val shader = GLES30.glCreateShader(type)
        GLES30.glShaderSource(shader, source)
        GLES30.glCompileShader(shader)
        val ok = IntArray(1)
        GLES30.glGetShaderiv(shader, GLES30.GL_COMPILE_STATUS, ok, 0)
        check(ok[0] != 0) { "shader: ${GLES30.glGetShaderInfoLog(shader)}" }
        return shader
    }

    private fun link(vertex: Int, fragment: Int): Int {
        val p = GLES30.glCreateProgram()
        GLES30.glAttachShader(p, vertex)
        GLES30.glAttachShader(p, fragment)
        GLES30.glLinkProgram(p)
        val ok = IntArray(1)
        GLES30.glGetProgramiv(p, GLES30.GL_LINK_STATUS, ok, 0)
        check(ok[0] != 0) { "program: ${GLES30.glGetProgramInfoLog(p)}" }
        return p
    }

    private companion object {
        const val EGL_RECORDABLE_ANDROID = 0x3142

        const val VERTEX = """#version 300 es
in vec2 aPos;
uniform mat4 uTex;
out vec2 vTex;
void main() {
    gl_Position = vec4(aPos, 0.0, 1.0);
    vTex = (uTex * vec4(aPos * 0.5 + 0.5, 0.0, 1.0)).xy;
}
"""

        // Tetrahedral interpolation with the same tetrahedra as Lut3D.lookup: step along the axis
        // with the largest fraction first, ties going red, green, blue.
        const val FRAGMENT = """#version 300 es
#extension GL_OES_EGL_image_external_essl3 : require
precision highp float;
precision highp sampler3D;
uniform samplerExternalOES uCamera;
uniform sampler3D uLut;
uniform float uLutSize;
in vec2 vTex;
out vec4 fragColor;

vec3 at(ivec3 p) { return texelFetch(uLut, p, 0).rgb; }

vec3 tetrahedral(vec3 c) {
    float n1 = uLutSize - 1.0;
    vec3 x = clamp(c, 0.0, 1.0) * n1;
    vec3 i0 = min(floor(x), vec3(n1 - 1.0));
    vec3 f = x - i0;
    ivec3 b = ivec3(i0);
    ivec3 e0; ivec3 e1; vec3 s;
    if (f.r >= f.g) {
        if (f.g >= f.b)      { e0 = ivec3(1, 0, 0); e1 = ivec3(0, 1, 0); s = f.rgb; }
        else if (f.r >= f.b) { e0 = ivec3(1, 0, 0); e1 = ivec3(0, 0, 1); s = f.rbg; }
        else                 { e0 = ivec3(0, 0, 1); e1 = ivec3(1, 0, 0); s = f.brg; }
    } else {
        if (f.b > f.g)       { e0 = ivec3(0, 0, 1); e1 = ivec3(0, 1, 0); s = f.bgr; }
        else if (f.r >= f.b) { e0 = ivec3(0, 1, 0); e1 = ivec3(1, 0, 0); s = f.grb; }
        else                 { e0 = ivec3(0, 1, 0); e1 = ivec3(0, 0, 1); s = f.gbr; }
    }
    return (1.0 - s.x) * at(b) + (s.x - s.y) * at(b + e0) + (s.y - s.z) * at(b + e0 + e1)
        + s.z * at(b + ivec3(1));
}

void main() {
    vec3 rgb = texture(uCamera, vTex).rgb;
    fragColor = vec4(tetrahedral(rgb), 1.0);
}
"""
    }
}

private const val TAG = "FilmSim"
