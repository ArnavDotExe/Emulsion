// The look model shared by the camera and the editor: the .fsim.json reader and a CPU
// reference renderer, plain Kotlin so its tests run on the JVM against colorlab's fixtures.
plugins {
    alias(libs.plugins.kotlin.jvm)
}

kotlin {
    jvmToolchain(21)
}

dependencies {
    implementation(libs.kotlinx.serialization.json)
    testImplementation(libs.kotlin.test.junit)
    testImplementation(libs.junit)
}
