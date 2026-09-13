<?php

declare(strict_types=1);

use MemoryMap\BigNatural;
use MemoryMap\DatabaseReportPdf;
use MemoryMap\EventStore;
use MemoryMap\SequentialStringId;

require_once dirname(__DIR__) . '/src/bootstrap.php';

$tests = [];

$tests['BigNatural arithmetic crosses native integer range'] = static function (): void {
    assertSame('100000000000000000000', BigNatural::add('99999999999999999999', '1'));
    assertSame('99999999999999999999', BigNatural::subtract('100000000000000000000', '1'));
    assertSame('121851850745626407', BigNatural::multiplySmall('123456789002661', 987));
    assertSame(['123456789002661', 0], BigNatural::divideSmall('121851850745626407', 987));
    assertSame(1, BigNatural::compare('10000000000000000001', '10000000000000000000'));
};

$tests['shortlex sequence has known base-2 ordinals'] = static function (): void {
    $sequence = new SequentialStringId('ab');
    $expected = ['' => '0', 'a' => '1', 'b' => '2', 'aa' => '3', 'ab' => '4', 'ba' => '5', 'bb' => '6'];

    foreach ($expected as $string => $id) {
        assertSame($id, $sequence->encodeVerified($string));
        assertSame($string, $sequence->decode($id));
    }
};

$tests['Unicode alphabet round-trips exactly'] = static function (): void {
    $sequence = new SequentialStringId('αβ🙂🚀 ');
    $input = '🚀 α🙂β🚀';
    $id = $sequence->encodeVerified($input);

    assertSame($input, $sequence->decode($id));
};

$tests['long input produces a reversible ID larger than PHP integers'] = static function (): void {
    $sequence = new SequentialStringId('01');
    $input = str_repeat('10110010', 60);
    $id = $sequence->encodeVerified($input);

    assertTrue(strlen($id) > strlen((string) PHP_INT_MAX), 'Expected a decimal ID larger than PHP_INT_MAX.');
    assertSame($input, $sequence->decode($id));
};

$tests['duplicate symbols and symbols outside the alphabet are rejected'] = static function (): void {
    assertThrows(static fn () => new SequentialStringId('aab'), InvalidArgumentException::class);
    $sequence = new SequentialStringId('ab');
    assertThrows(static fn () => $sequence->encode('abc'), InvalidArgumentException::class);
};

$tests['event store persists nodes and rebuilds a square array'] = static function (): void {
    $path = sys_get_temp_dir() . DIRECTORY_SEPARATOR . 'memory-square-' . bin2hex(random_bytes(6)) . '.json';
    try {
        $store = new EventStore($path);
        for ($index = 0; $index < 3; $index++) {
            $store->append([
                'time-stamp' => EventStore::timestamp(),
                'label' => 'Node ' . $index,
                'value' => (string) $index,
                'ID' => 'node-' . $index,
                'sequential-string ID' => (string) ($index + 1),
            ]);
        }

        $database = $store->read();
        assertSame(3, count($database['events']));
        assertSame(['rows' => 2, 'columns' => 2], $database['map']['dimensions']);
        assertSame(4, $database['map']['cells-total']);
        assertSame(3, $database['map']['cells-occupied']);
        assertSame(1, $database['map']['cells-vacant']);
        assertSame([['node-0', 'node-1'], ['node-2', null]], $database['map']['cells']);
    } finally {
        if (is_file($path)) {
            unlink($path);
        }
    }
};

$tests['PDF report includes database, map, event, and Unicode details'] = static function (): void {
    $database = [
        'schema' => 'memory-event-node/1.0',
        'updated-at' => '2026-07-16T12:00:00.000Z',
        'events' => [[
            'time-stamp' => '2026-07-16T11:59:00.000Z',
            'label' => 'Unicode memory α🙂',
            'value' => "line one\nline two",
            'ID' => 'node-1',
            'sequential-string ID' => str_repeat('1234567890', 24),
            'alphabet' => 'αβ🙂 ',
            'metrics' => ['value-symbols' => 18, 'native-integer' => false],
        ]],
        'map' => [
            'dimensions' => ['rows' => 1, 'columns' => 2],
            'cells' => [['node-1', null]],
        ],
    ];

    $pdf = (new DatabaseReportPdf($database, 'events.json', '2026-07-16T12:01:00.000Z'))->render();

    assertTrue(str_starts_with($pdf, '%PDF-1.4'), 'Expected a PDF 1.4 document.');
    assertTrue(str_contains($pdf, 'MAP.CELLS[0][1]'), 'Expected vacant map coordinates in the report.');
    assertTrue(str_contains($pdf, 'SEQUENTIAL-STRING ID'), 'Expected full event fields in the report.');
    assertTrue(str_contains($pdf, '<U+03B1>') && str_contains($pdf, '<U+1F642>'), 'Expected lossless Unicode code-point notation.');
    assertTrue(str_contains($pdf, 'PAGE 1 OF'), 'Expected numbered page footers.');
    assertTrue(str_ends_with($pdf, "%%EOF\n"), 'Expected a complete PDF trailer.');
};

$tests['production database contains only complete reversible event records'] = static function (): void {
    $database = (new EventStore(dirname(__DIR__) . '/data/events.json'))->read();
    assertSame('memory-event-node/2.0', $database['schema']);
    assertSame(209, count($database['events']));
    assertSame(209, $database['map']['cells-occupied']);
    assertSame(225, $database['map']['cells-total']);
    assertSame(16, $database['map']['cells-vacant']);

    foreach ($database['events'] as $index => $event) {
        foreach ([
            'time-stamp', 'label', 'value', 'ID', 'sequential-string ID',
            'backward-computed value', 'alphabet', 'metrics', 'instruction',
        ] as $field) {
            assertTrue(array_key_exists($field, $event), sprintf('Event %d lacks %s.', $index + 1, $field));
        }
        $sequence = new SequentialStringId($event['alphabet']);
        assertSame($event['sequential-string ID'], $sequence->encodeVerified($event['value']));
        assertSame($event['value'], $sequence->decode($event['sequential-string ID']));
        assertSame($event['value'], $event['backward-computed value']);
        assertSame(true, $event['backward-computation']['matches-value']);
        assertSame(mb_strlen($event['value']), $event['metrics']['value-symbols']);
        assertSame(strlen($event['sequential-string ID']), $event['metrics']['sequential-ID-digits']);
    }
};

$failures = 0;
foreach ($tests as $name => $test) {
    try {
        $test();
        echo "PASS  {$name}" . PHP_EOL;
    } catch (Throwable $exception) {
        $failures++;
        echo "FAIL  {$name}" . PHP_EOL;
        echo '      ' . $exception::class . ': ' . $exception->getMessage() . PHP_EOL;
    }
}

echo PHP_EOL . sprintf('%d passed, %d failed', count($tests) - $failures, $failures) . PHP_EOL;
exit($failures === 0 ? 0 : 1);

function assertSame(mixed $expected, mixed $actual): void
{
    if ($expected !== $actual) {
        throw new RuntimeException(sprintf(
            'Expected %s; got %s.',
            var_export($expected, true),
            var_export($actual, true),
        ));
    }
}

function assertTrue(bool $condition, string $message): void
{
    if (!$condition) {
        throw new RuntimeException($message);
    }
}

/** @param class-string<Throwable> $expectedClass */
function assertThrows(callable $operation, string $expectedClass): void
{
    try {
        $operation();
    } catch (Throwable $exception) {
        if ($exception instanceof $expectedClass) {
            return;
        }
        throw new RuntimeException(sprintf('Expected %s; got %s.', $expectedClass, $exception::class));
    }

    throw new RuntimeException(sprintf('Expected %s to be thrown.', $expectedClass));
}
