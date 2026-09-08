import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpHandler;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.lang.management.GarbageCollectorMXBean;
import java.lang.management.ManagementFactory;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.AtomicMoveNotSupportedException;
import java.nio.file.StandardCopyOption;
import java.nio.file.StandardOpenOption;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicLong;
import java.util.concurrent.locks.LockSupport;
import java.util.concurrent.locks.ReentrantLock;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public final class Hotspot {
    private static final FaultSwitch CPU_FAULT = new FaultSwitch();
    private static final FaultSwitch GC_FAULT = new FaultSwitch();
    private static final FaultSwitch LOCK_FAULT = new FaultSwitch();
    private static final FaultSwitch DOWNSTREAM_FAULT = new FaultSwitch();
    private static final FaultSwitch OFFHEAP_FAULT = new FaultSwitch();
    private static final FaultSwitch IO_FAULT = new FaultSwitch();

    private static final AtomicLong CPU_OPERATIONS = new AtomicLong();
    private static final AtomicLong ALLOCATED_BYTES = new AtomicLong();
    private static final AtomicLong LOCK_ACQUISITIONS = new AtomicLong();
    private static final AtomicLong LOCK_CONTENTIONS = new AtomicLong();
    private static final AtomicLong LOCK_WAIT_NANOS = new AtomicLong();
    private static final AtomicLong DOWNSTREAM_REQUESTS = new AtomicLong();
    private static final AtomicLong DOWNSTREAM_FAILURES = new AtomicLong();
    private static final AtomicLong DOWNSTREAM_LATENCY_NANOS = new AtomicLong();
    private static final AtomicLong DOWNSTREAM_DELAY_MILLIS = new AtomicLong(260);
    private static final AtomicLong OFFHEAP_TARGET_BYTES = new AtomicLong(96L * 1_024 * 1_024);
    private static final AtomicLong OFFHEAP_RETAINED_BYTES = new AtomicLong();
    private static final AtomicLong IO_BYTES_WRITTEN = new AtomicLong();
    private static final AtomicLong IO_OPERATIONS = new AtomicLong();
    private static final AtomicLong IO_FAILURES = new AtomicLong();
    private static final ReentrantLock DEMO_LOCK = new ReentrantLock();
    private static final List<ByteBuffer> DIRECT_RETAINED =
        Collections.synchronizedList(new ArrayList<>());
    private static final Object IO_LOCK = new Object();
    private static final Path IO_PATH = Path.of("/tmp/mini-drop-java-io-fault.bin");
    private static final Path METRICS_PATH = Path.of("/tmp/mini-drop-jvm-metrics.json");
    private static final long IO_MAX_FILE_BYTES = 64L * 1_024 * 1_024;

    private static final class FaultSwitch {
        private final AtomicBoolean active = new AtomicBoolean(false);
        private final AtomicLong deadlineMillis = new AtomicLong(0);

        void start(long durationSeconds) {
            long bounded = Math.max(15, Math.min(durationSeconds, 300));
            deadlineMillis.set(System.currentTimeMillis() + bounded * 1_000);
            active.set(true);
        }

        void stop() {
            active.set(false);
            deadlineMillis.set(0);
        }

        boolean isActive() {
            if (!active.get()) return false;
            long deadline = deadlineMillis.get();
            if (deadline > 0 && System.currentTimeMillis() >= deadline) {
                stop();
                return false;
            }
            return true;
        }

        double remainingSeconds() {
            if (!isActive()) return 0.0;
            return Math.max(0.0, (deadlineMillis.get() - System.currentTimeMillis()) / 1_000.0);
        }
    }

    private static long hostPid() {
        try {
            for (String line : Files.readAllLines(Path.of("/proc/self/status"))) {
                if (line.startsWith("NSpid:")) {
                    List<String> values = List.of(line.substring(6).trim().split("\\s+"));
                    if (!values.isEmpty()) return Long.parseLong(values.get(0));
                }
            }
        } catch (Exception ignored) {
            // Linux without NSpid falls back to the namespace-local PID.
        }
        return ProcessHandle.current().pid();
    }

    private static void respond(HttpExchange exchange, int status, String json) throws IOException {
        byte[] body = json.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json; charset=utf-8");
        exchange.sendResponseHeaders(status, body.length);
        exchange.getResponseBody().write(body);
        exchange.close();
    }

    private static boolean requirePost(HttpExchange exchange) throws IOException {
        if ("POST".equals(exchange.getRequestMethod())) return true;
        respond(exchange, 405, "{\"error\":\"method_not_allowed\"}");
        return false;
    }

    private static String requestBody(HttpExchange exchange) throws IOException {
        return new String(exchange.getRequestBody().readNBytes(4_096), StandardCharsets.UTF_8);
    }

    private static long option(String payload, String key, long fallback) {
        Matcher matcher = Pattern.compile("\\\"" + Pattern.quote(key) + "\\\"\\s*:\\s*(\\d+)")
            .matcher(payload);
        return matcher.find() ? Long.parseLong(matcher.group(1)) : fallback;
    }

    private static long option(HttpExchange exchange, String key, long fallback) throws IOException {
        return option(requestBody(exchange), key, fallback);
    }

    private static String snapshot() {
        long requests = DOWNSTREAM_REQUESTS.get();
        double latencyMillis = requests == 0
            ? 0.0
            : DOWNSTREAM_LATENCY_NANOS.get() / (double) requests / 1_000_000.0;
        long gcCount = 0;
        long gcTimeMillis = 0;
        for (GarbageCollectorMXBean collector : ManagementFactory.getGarbageCollectorMXBeans()) {
            if (collector.getCollectionCount() > 0) gcCount += collector.getCollectionCount();
            if (collector.getCollectionTime() > 0) gcTimeMillis += collector.getCollectionTime();
        }
        Runtime runtime = Runtime.getRuntime();
        long heapUsed = runtime.totalMemory() - runtime.freeMemory();
        boolean cpuActive = CPU_FAULT.isActive();
        boolean gcActive = GC_FAULT.isActive();
        boolean lockActive = LOCK_FAULT.isActive();
        boolean downstreamActive = DOWNSTREAM_FAULT.isActive();
        boolean offheapActive = OFFHEAP_FAULT.isActive();
        boolean ioActive = IO_FAULT.isActive();
        return "{"
            + "\"schema_version\":\"mini-drop.application-metrics.v1\""
            + ",\"status\":\"ok\",\"runtime\":\"java\",\"pid\":" + ProcessHandle.current().pid()
            + ",\"host_pid\":" + hostPid()
            + ",\"fault_active\":" + cpuActive
            + ",\"cpu_fault_active\":" + cpuActive
            + ",\"cpu_operations\":" + CPU_OPERATIONS.get()
            + ",\"cpu_auto_stop_remaining_seconds\":" + CPU_FAULT.remainingSeconds()
            + ",\"gc_fault_active\":" + gcActive
            + ",\"allocated_bytes\":" + ALLOCATED_BYTES.get()
            + ",\"heap_used_bytes\":" + heapUsed
            + ",\"gc_count\":" + gcCount
            + ",\"gc_time_ms\":" + gcTimeMillis
            + ",\"gc_auto_stop_remaining_seconds\":" + GC_FAULT.remainingSeconds()
            + ",\"lock_fault_active\":" + lockActive
            + ",\"lock_acquisitions\":" + LOCK_ACQUISITIONS.get()
            + ",\"lock_contentions\":" + LOCK_CONTENTIONS.get()
            + ",\"lock_wait_ms\":" + (LOCK_WAIT_NANOS.get() / 1_000_000.0)
            + ",\"lock_auto_stop_remaining_seconds\":" + LOCK_FAULT.remainingSeconds()
            + ",\"downstream_fault_active\":" + downstreamActive
            + ",\"downstream_delay_ms\":" + DOWNSTREAM_DELAY_MILLIS.get()
            + ",\"downstream_requests\":" + requests
            + ",\"downstream_failures\":" + DOWNSTREAM_FAILURES.get()
            + ",\"downstream_average_latency_ms\":" + latencyMillis
            + ",\"downstream_auto_stop_remaining_seconds\":" + DOWNSTREAM_FAULT.remainingSeconds()
            + ",\"offheap_fault_active\":" + offheapActive
            + ",\"offheap_retained_bytes\":" + OFFHEAP_RETAINED_BYTES.get()
            + ",\"offheap_auto_stop_remaining_seconds\":" + OFFHEAP_FAULT.remainingSeconds()
            + ",\"io_fault_active\":" + ioActive
            + ",\"io_bytes_written\":" + IO_BYTES_WRITTEN.get()
            + ",\"io_operations\":" + IO_OPERATIONS.get()
            + ",\"io_failures\":" + IO_FAILURES.get()
            + ",\"io_auto_stop_remaining_seconds\":" + IO_FAULT.remainingSeconds()
            + "}";
    }

    private static byte[] javaCpuHotFunction(MessageDigest digest, byte[] value) {
        for (int index = 0; index < 500; index++) value = digest.digest(value);
        CPU_OPERATIONS.addAndGet(500);
        return value;
    }

    private static void startDaemon(String name, Runnable runnable) {
        Thread thread = new Thread(runnable, name);
        thread.setDaemon(true);
        thread.start();
    }

    private static void publishMetricsSnapshot() {
        Path temporary = METRICS_PATH.resolveSibling(METRICS_PATH.getFileName() + ".tmp");
        try {
            Files.writeString(
                temporary,
                snapshot(),
                StandardCharsets.UTF_8,
                StandardOpenOption.CREATE,
                StandardOpenOption.TRUNCATE_EXISTING,
                StandardOpenOption.WRITE
            );
            try {
                Files.move(
                    temporary,
                    METRICS_PATH,
                    StandardCopyOption.ATOMIC_MOVE,
                    StandardCopyOption.REPLACE_EXISTING
                );
            } catch (AtomicMoveNotSupportedException ignored) {
                Files.move(temporary, METRICS_PATH, StandardCopyOption.REPLACE_EXISTING);
            }
        } catch (IOException ignored) {
            // The demo remains usable even when the optional counter sidecar
            // cannot be written; the Agent will then preserve that limitation.
        }
    }

    private static void clearDirectMemory() {
        synchronized (DIRECT_RETAINED) {
            DIRECT_RETAINED.clear();
            OFFHEAP_RETAINED_BYTES.set(0);
        }
        // Direct buffers are reclaimed by their Cleaner. This is a bounded
        // demo-only request after references have been dropped, not a loop.
        System.gc();
    }

    private static void cleanupJavaIO() {
        synchronized (IO_LOCK) {
            try {
                Files.deleteIfExists(IO_PATH);
            } catch (IOException ignored) {
                IO_FAILURES.incrementAndGet();
            }
        }
    }

    private static boolean callSlowLoopback() {
        try (Socket socket = new Socket()) {
            socket.connect(new InetSocketAddress("127.0.0.1", 8082), 1_000);
            socket.setSoTimeout(2_000);
            OutputStream output = socket.getOutputStream();
            output.write("GET /work HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n"
                .getBytes(StandardCharsets.US_ASCII));
            output.flush();
            InputStream input = socket.getInputStream();
            return input.read() >= 0;
        } catch (IOException exception) {
            return false;
        }
    }

    private static void startWorkers() {
        startDaemon("mini-drop-java-cpu", () -> {
            try {
                MessageDigest digest = MessageDigest.getInstance("SHA-256");
                byte[] value = "mini-drop-java-hotspot".getBytes(StandardCharsets.UTF_8);
                while (true) {
                    if (!CPU_FAULT.isActive()) {
                        LockSupport.parkNanos(25_000_000L);
                        continue;
                    }
                    value = javaCpuHotFunction(digest, value);
                }
            } catch (Exception exception) {
                throw new IllegalStateException(exception);
            }
        });

        startDaemon("mini-drop-java-allocation", () -> {
            byte[][] retainedRing = new byte[32][];
            int slot = 0;
            while (true) {
                if (!GC_FAULT.isActive()) {
                    Arrays.fill(retainedRing, null);
                    LockSupport.parkNanos(25_000_000L);
                    continue;
                }
                byte[] allocation = new byte[256 * 1_024];
                for (int index = 0; index < allocation.length; index += 4_096) allocation[index] = 1;
                retainedRing[slot++ % retainedRing.length] = allocation;
                ALLOCATED_BYTES.addAndGet(allocation.length);
                LockSupport.parkNanos(2_000_000L);
            }
        });

        startDaemon("mini-drop-java-lock-owner", () -> {
            while (true) {
                if (!LOCK_FAULT.isActive()) {
                    LockSupport.parkNanos(25_000_000L);
                    continue;
                }
                DEMO_LOCK.lock();
                try {
                    LockSupport.parkNanos(2_000_000L);
                    LOCK_ACQUISITIONS.incrementAndGet();
                } finally {
                    DEMO_LOCK.unlock();
                }
                LockSupport.parkNanos(300_000L);
            }
        });
        for (int worker = 0; worker < 4; worker++) {
            startDaemon("mini-drop-java-lock-waiter-" + worker, () -> {
                while (true) {
                    if (!LOCK_FAULT.isActive()) {
                        LockSupport.parkNanos(25_000_000L);
                        continue;
                    }
                    long started = System.nanoTime();
                    if (!DEMO_LOCK.tryLock()) {
                        LOCK_CONTENTIONS.incrementAndGet();
                        DEMO_LOCK.lock();
                    }
                    try {
                        LOCK_ACQUISITIONS.incrementAndGet();
                    } finally {
                        LOCK_WAIT_NANOS.addAndGet(System.nanoTime() - started);
                        DEMO_LOCK.unlock();
                    }
                }
            });
        }

        for (int worker = 0; worker < 4; worker++) {
            startDaemon("mini-drop-java-downstream-" + worker, () -> {
                while (true) {
                    if (!DOWNSTREAM_FAULT.isActive()) {
                        LockSupport.parkNanos(25_000_000L);
                        continue;
                    }
                    long started = System.nanoTime();
                    if (!callSlowLoopback()) {
                        DOWNSTREAM_FAILURES.incrementAndGet();
                    }
                    DOWNSTREAM_REQUESTS.incrementAndGet();
                    DOWNSTREAM_LATENCY_NANOS.addAndGet(System.nanoTime() - started);
                    LockSupport.parkNanos(20_000_000L);
                }
            });
        }

        startDaemon("mini-drop-java-offheap", () -> {
            boolean wasActive = false;
            while (true) {
                if (!OFFHEAP_FAULT.isActive()) {
                    if (wasActive) {
                        clearDirectMemory();
                        wasActive = false;
                    }
                    LockSupport.parkNanos(50_000_000L);
                    continue;
                }
                wasActive = true;
                if (OFFHEAP_RETAINED_BYTES.get() < OFFHEAP_TARGET_BYTES.get()) {
                    ByteBuffer buffer = ByteBuffer.allocateDirect(2 * 1_024 * 1_024);
                    for (int offset = 0; offset < buffer.capacity(); offset += 4_096) {
                        buffer.put(offset, (byte) 1);
                    }
                    synchronized (DIRECT_RETAINED) {
                        if (OFFHEAP_FAULT.isActive()
                            && OFFHEAP_RETAINED_BYTES.get() < OFFHEAP_TARGET_BYTES.get()) {
                            DIRECT_RETAINED.add(buffer);
                            OFFHEAP_RETAINED_BYTES.addAndGet(buffer.capacity());
                        }
                    }
                }
                LockSupport.parkNanos(180_000_000L);
            }
        });

        startDaemon("mini-drop-java-io", () -> {
            byte[] bytes = new byte[128 * 1_024];
            byte[] marker = "mini-drop-java-synchronous-io".getBytes(StandardCharsets.UTF_8);
            System.arraycopy(marker, 0, bytes, 0, marker.length);
            boolean wasActive = false;
            while (true) {
                if (!IO_FAULT.isActive()) {
                    if (wasActive) {
                        cleanupJavaIO();
                        wasActive = false;
                    }
                    LockSupport.parkNanos(40_000_000L);
                    continue;
                }
                wasActive = true;
                synchronized (IO_LOCK) {
                    if (!IO_FAULT.isActive()) continue;
                    try (FileChannel channel = FileChannel.open(
                        IO_PATH,
                        StandardOpenOption.CREATE,
                        StandardOpenOption.WRITE,
                        StandardOpenOption.APPEND
                    )) {
                        ByteBuffer payload = ByteBuffer.wrap(bytes);
                        while (payload.hasRemaining()) channel.write(payload);
                        channel.force(true);
                        IO_BYTES_WRITTEN.addAndGet(bytes.length);
                        IO_OPERATIONS.incrementAndGet();
                        if (channel.size() >= IO_MAX_FILE_BYTES) {
                            channel.close();
                            Files.deleteIfExists(IO_PATH);
                        }
                    } catch (IOException exception) {
                        IO_FAILURES.incrementAndGet();
                        IO_FAULT.stop();
                    }
                }
                LockSupport.parkNanos(20_000_000L);
            }
        });
    }

    private static HttpHandler startHandler(FaultSwitch fault, Runnable reset) {
        return exchange -> {
            if (!requirePost(exchange)) return;
            long duration = option(exchange, "duration_seconds", 60);
            fault.stop();
            reset.run();
            fault.start(duration);
            respond(exchange, 200, snapshot());
        };
    }

    private static HttpHandler stopHandler(FaultSwitch fault) {
        return exchange -> {
            if (!requirePost(exchange)) return;
            fault.stop();
            respond(exchange, 200, snapshot());
        };
    }

    public static void main(String[] args) throws Exception {
        startWorkers();
        startDaemon("mini-drop-jvm-metrics", () -> {
            while (true) {
                publishMetricsSnapshot();
                LockSupport.parkNanos(200_000_000L);
            }
        });
        HttpServer server = HttpServer.create(new InetSocketAddress(8082), 0);
        server.setExecutor(Executors.newCachedThreadPool());
        server.createContext("/health", exchange -> respond(exchange, 200, snapshot()));
        server.createContext("/snapshot", exchange -> respond(exchange, 200, snapshot()));
        server.createContext("/work", exchange -> {
            if (DOWNSTREAM_FAULT.isActive()) {
                try {
                    Thread.sleep(DOWNSTREAM_DELAY_MILLIS.get());
                } catch (InterruptedException interrupted) {
                    Thread.currentThread().interrupt();
                }
            }
            respond(exchange, 200, "{\"status\":\"ok\"}");
        });
        server.createContext("/faults/cpu/start", startHandler(CPU_FAULT, () -> CPU_OPERATIONS.set(0)));
        server.createContext("/faults/cpu/stop", stopHandler(CPU_FAULT));
        server.createContext("/faults/gc/start", startHandler(GC_FAULT, () -> ALLOCATED_BYTES.set(0)));
        server.createContext("/faults/gc/stop", stopHandler(GC_FAULT));
        server.createContext("/faults/lock/start", startHandler(LOCK_FAULT, () -> {
            LOCK_ACQUISITIONS.set(0);
            LOCK_CONTENTIONS.set(0);
            LOCK_WAIT_NANOS.set(0);
        }));
        server.createContext("/faults/lock/stop", stopHandler(LOCK_FAULT));
        server.createContext("/faults/downstream/start", exchange -> {
            if (!requirePost(exchange)) return;
            String payload = new String(exchange.getRequestBody().readNBytes(4_096), StandardCharsets.UTF_8);
            Matcher duration = Pattern.compile("\\\"duration_seconds\\\"\\s*:\\s*(\\d+)").matcher(payload);
            Matcher delay = Pattern.compile("\\\"delay_ms\\\"\\s*:\\s*(\\d+)").matcher(payload);
            long boundedDelay = delay.find() ? Long.parseLong(delay.group(1)) : 260;
            DOWNSTREAM_DELAY_MILLIS.set(Math.max(50, Math.min(boundedDelay, 1_000)));
            DOWNSTREAM_REQUESTS.set(0);
            DOWNSTREAM_FAILURES.set(0);
            DOWNSTREAM_LATENCY_NANOS.set(0);
            DOWNSTREAM_FAULT.start(duration.find() ? Long.parseLong(duration.group(1)) : 60);
            respond(exchange, 200, snapshot());
        });
        server.createContext("/faults/downstream/stop", stopHandler(DOWNSTREAM_FAULT));
        server.createContext("/faults/offheap/start", exchange -> {
            if (!requirePost(exchange)) return;
            String payload = requestBody(exchange);
            long megabytes = Math.max(16, Math.min(option(payload, "megabytes", 96), 128));
            OFFHEAP_FAULT.stop();
            clearDirectMemory();
            OFFHEAP_TARGET_BYTES.set(megabytes * 1_024 * 1_024);
            OFFHEAP_FAULT.start(option(payload, "duration_seconds", 60));
            respond(exchange, 200, snapshot());
        });
        server.createContext("/faults/offheap/stop", exchange -> {
            if (!requirePost(exchange)) return;
            OFFHEAP_FAULT.stop();
            clearDirectMemory();
            respond(exchange, 200, snapshot());
        });
        server.createContext("/faults/io/start", startHandler(IO_FAULT, () -> {
            cleanupJavaIO();
            IO_BYTES_WRITTEN.set(0);
            IO_OPERATIONS.set(0);
            IO_FAILURES.set(0);
        }));
        server.createContext("/faults/io/stop", exchange -> {
            if (!requirePost(exchange)) return;
            IO_FAULT.stop();
            cleanupJavaIO();
            respond(exchange, 200, snapshot());
        });
        server.start();
    }
}
