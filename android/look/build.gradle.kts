// The look model shared by the camera and the editor: the .fsim.json reader and a CPU
// reference renderer, plain Kotlin so its tests run on the JVM against colorlab's fixtures.
import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    alias(libs.plugins.kotlin.jvm)
}

java {
    sourceCompatibility = JavaVersion.VERSION_17
    targetCompatibility = JavaVersion.VERSION_17
}

kotlin {
    jvmToolchain(21)
    compilerOptions { jvmTarget.set(JvmTarget.JVM_17) } // the app's Java level
}
dependencies {
    implementation(libs.kotlinx.serialization.json)
    testImplementation(libs.kotlin.test.junit)
    testImplementation(libs.junit)
}

tasks.test {
    // The looks bundled in the app, checked by ShippedLooksTest.
    val shipped = rootProject.layout.projectDirectory.dir("app/src/main/assets/looks")
    inputs.dir(shipped).withPropertyName("shippedLooks")
    systemProperty("filmsim.shippedLooks", shipped.asFile.absolutePath)
}
