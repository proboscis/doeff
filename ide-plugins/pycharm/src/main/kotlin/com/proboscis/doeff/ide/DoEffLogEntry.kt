package com.proboscis.doeff.ide

import java.time.Instant

data class DoEffLogEntry(
    val level: DoEffLogLevel,
    val message: String,
    val details: String? = null,
    val throwable: Throwable? = null,
    val timestamp: Instant = Instant.now()
)

enum class DoEffLogLevel {
    INFO,
    WARN,
    ERROR
}
