import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.lang.management.ManagementFactory;
import java.lang.management.MemoryUsage;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicLong;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** A bounded Java target used by the real GC diagnosis campaign. */
public final class HotspotApp {
    private static final AtomicBoolean GC_FAULT_ACTIVE = new AtomicBoolean(false);
    private static final AtomicLong INJECTED_GC_CYCLES = new AtomicLong();
    private static final AtomicLong ALLOCATED_BYTES = new AtomicLong();
    private static final AtomicLong LAST_GC_PAUSE_NS = new AtomicLong();
    private static final AtomicLong MAX_GC_PAUSE_NS = new AtomicLong();
    private static final Pattern DURATION_PATTERN = Pattern.compile(
        "\\\"duration_seconds\\\"\\s*:\\s*([0-9]+(?:\\.[0-9]+)?)"
    );
    private static volatile Thread gcFaultThread;
    private static volatile long sink;

    private static long mix(long value) {
        value ^= value << 13;
        value ^= value >>> 7;
        value ^= value << 17;
        return value;
    }

    private static void cpuHotLoop() {
        long value = System.nanoTime();
        while (!Thread.currentThread().isInterrupted()) {
            for (int index = 0; index < 500_000; index++) {
                value = mix(value + index);
            }
            sink = value;
            try {
                Thread.sleep(10);
            } catch (InterruptedException interrupted) {
                Thread.currentThread().interrupt();
            }
        }
    }

    private static synchronized void startGcFault(double durationSeconds) {
        stopGcFault();
        GC_FAULT_ACTIVE.set(true);
        long deadline = System.nanoTime() + (long) (Math.max(2.0, durationSeconds) * 1_000_000_000L);
        Thread worker = new Thread(() -> {
            try {
                while (GC_FAULT_ACTIVE.get() && System.nanoTime() < deadline) {
                    List<byte[]> shortLived = new ArrayList<>();
                    for (int index = 0; index < 64; index++) {
                        shortLived.add(new byte[256 * 1024]);
                        ALLOCATED_BYTES.addAndGet(256L * 1024L);
                    }
                    sink ^= shortLived.size();
                    long started = System.nanoTime();
                    System.gc();
                    long elapsed = System.nanoTime() - started;
                    LAST_GC_PAUSE_NS.set(elapsed);
                    MAX_GC_PAUSE_NS.accumulateAndGet(elapsed, Math::max);
                    INJECTED_GC_CYCLES.incrementAndGet();
                    Thread.sleep(40);
                }
            } catch (InterruptedException interrupted) {
                Thread.currentThread().interrupt();
            } finally {
                GC_FAULT_ACTIVE.set(false);
            }
        }, "bounded-manual-full-gc-pressure");
        worker.setDaemon(true);
        gcFaultThread = worker;
        worker.start();
    }

    private static synchronized void stopGcFault() {
        GC_FAULT_ACTIVE.set(false);
        Thread worker = gcFaultThread;
        gcFaultThread = null;
        if (worker != null) {
            worker.interrupt();
        }
    }

    private static long hostPid() {
        try {
            for (String line : Files.readAllLines(Path.of("/proc/self/status"))) {
                if (line.startsWith("NSpid:")) {
                    String[] parts = line.substring("NSpid:".length()).trim().split("\\s+");
                    return Long.parseLong(parts[parts.length - 1]);
                }
            }
        } catch (Exception ignored) {
            // ProcessHandle is still correct when the container shares the host PID namespace.
        }
        return ProcessHandle.current().pid();
    }

    private static long totalGcCount() {
        long total = 0;
        for (java.lang.management.GarbageCollectorMXBean bean : ManagementFactory.getGarbageCollectorMXBeans()) {
            if (bean.getCollectionCount() > 0) {
                total += bean.getCollectionCount();
            }
        }
        return total;
    }

    private static long totalGcTimeMs() {
        long total = 0;
        for (java.lang.management.GarbageCollectorMXBean bean : ManagementFactory.getGarbageCollectorMXBeans()) {
            if (bean.getCollectionTime() > 0) {
                total += bean.getCollectionTime();
            }
        }
        return total;
    }

    private static String snapshotJson() {
        MemoryUsage heap = ManagementFactory.getMemoryMXBean().getHeapMemoryUsage();
        return String.format(Locale.ROOT,
            "{\"status\":\"ok\",\"pid\":%d,\"host_pid\":%d," +
            "\"gc_fault_active\":%s,\"gc_collection_count\":%d,\"gc_collection_time_ms\":%d," +
            "\"injected_gc_cycles\":%d,\"allocated_bytes\":%d," +
            "\"heap_used_mb\":%.3f,\"heap_committed_mb\":%.3f," +
            "\"last_gc_pause_ms\":%.3f,\"max_gc_pause_ms\":%.3f}",
            ProcessHandle.current().pid(), hostPid(), GC_FAULT_ACTIVE.get(), totalGcCount(), totalGcTimeMs(),
            INJECTED_GC_CYCLES.get(), ALLOCATED_BYTES.get(),
            heap.getUsed() / 1048576.0, heap.getCommitted() / 1048576.0,
            LAST_GC_PAUSE_NS.get() / 1_000_000.0, MAX_GC_PAUSE_NS.get() / 1_000_000.0
        );
    }

    private static double readDuration(HttpExchange exchange) throws IOException {
        String body = new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
        Matcher matcher = DURATION_PATTERN.matcher(body);
        return matcher.find() ? Double.parseDouble(matcher.group(1)) : 8.0;
    }

    private static void json(HttpExchange exchange, int status, String payload) throws IOException {
        byte[] bytes = payload.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json; charset=utf-8");
        exchange.sendResponseHeaders(status, bytes.length);
        exchange.getResponseBody().write(bytes);
        exchange.close();
    }

    public static void main(String[] args) throws Exception {
        Thread cpu = new Thread(HotspotApp::cpuHotLoop, "java-background-workload");
        cpu.setDaemon(true);
        cpu.start();

        HttpServer server = HttpServer.create(new InetSocketAddress(7070), 0);
        server.createContext("/health", exchange -> json(exchange, 200, "{\"status\":\"ok\"}"));
        server.createContext("/snapshot", exchange -> json(exchange, 200, snapshotJson()));
        server.createContext("/faults/gc/start", exchange -> {
            startGcFault(readDuration(exchange));
            json(exchange, 200, snapshotJson());
        });
        server.createContext("/faults/gc/stop", exchange -> {
            stopGcFault();
            json(exchange, 200, snapshotJson());
        });
        server.setExecutor(Executors.newFixedThreadPool(4));
        server.start();
        System.out.println("Java GC campaign target started, pid=" + ProcessHandle.current().pid());
        Thread.currentThread().join();
    }
}
