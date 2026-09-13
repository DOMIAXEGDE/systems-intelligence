<?php
/** Execute pinned public source locally; never calls the website. */
declare(strict_types=1);
require_once __DIR__ . '/repository-src-BigNatural.php';
require_once __DIR__ . '/repository-src-SequentialStringId.php';
use MemoryMap\SequentialStringId;

$ascii = implode('', array_map('chr', range(32, 126)));
$cases = [
    ['empty', 'ab', ''], ['a', 'ab', 'a'], ['b', 'ab', 'b'],
    ['aa', 'ab', 'aa'], ['ab', 'ab', 'ab'], ['ba', 'ab', 'ba'], ['bb', 'ab', 'bb'],
    ['aaa-next-bucket', 'ab', 'aaa'],
    ['alphabet-order-changed', 'ba', 'ab'],
    ['leading-first-symbols', '01', '001'],
    ['unicode-repository-test', 'αβ🙂🚀 ', '🚀 α🙂β🚀'],
    ['unicode-composed', "é" . "e\u{0301}", 'é'],
    ['unicode-decomposed', "é" . "e\u{0301}", "e\u{0301}"],
    ['ascii-space', $ascii, ' '], ['ascii-A', $ascii, 'A'],
    ['ascii-tilde', $ascii, '~'], ['ascii-two-spaces', $ascii, '  '],
    ['ascii-punctuation-and-case', $ascii, "Hello, P&R!"],
    ['explicit-control-alphabet', "a\n\t ", "a\n\t "],
    ['long-repository-test', '01', str_repeat('10110010', 60)],
];
$output = [
    'schema' => 'pandr-shortlex-research-vectors/1.0',
    'provenance' => [
        'kind' => 'executed-pinned-public-PHP-source-offline',
        'repository' => 'https://github.com/DOMIAXEGDE/Language',
        'commit' => '31d13acd4329364f649d97f3a6806d5fa5b3d5d1',
        'codec' => 'src/SequentialStringId.php',
        'arithmetic' => 'src/BigNatural.php',
        'runtime' => PHP_VERSION,
        'deployed_server_tested' => false,
    ],
    'roundtrip' => [], 'decode_aliases' => [], 'rejections' => [],
];
foreach ($cases as [$name, $alphabet, $text]) {
    $codec = new SequentialStringId($alphabet);
    $id = $codec->encodeVerified($text);
    if ($codec->decode($id) !== $text) throw new RuntimeException('Proof failed.');
    $output['roundtrip'][] = compact('name', 'alphabet', 'text', 'id');
}
foreach (['000', '0004'] as $input) {
    $codec = new SequentialStringId('ab');
    $text = $codec->decode($input);
    $output['decode_aliases'][] = [
        'name' => 'leading-zero-' . $input, 'alphabet' => 'ab',
        'input_id' => $input, 'text' => $text, 'canonical_id' => $codec->encode($text),
    ];
}
$invalid = [
    ['empty-alphabet', '', 'construct', ''], ['unary-alphabet', 'a', 'construct', ''],
    ['duplicate-alphabet', 'aab', 'construct', ''], ['outside-alphabet', 'ab', 'encode', 'abc'],
    ['default-newline', $ascii, 'encode', "a\n"],
    ['empty-id', 'ab', 'decode', ''], ['negative-id', 'ab', 'decode', '-1'],
    ['plus-id', 'ab', 'decode', '+1'], ['space-id', 'ab', 'decode', ' 1'],
    ['decimal-point-id', 'ab', 'decode', '1.0'], ['exponent-id', 'ab', 'decode', '1e2'],
    ['unicode-digit-id', 'ab', 'decode', '١'], ['newline-id', 'ab', 'decode', "1\n"],
];
foreach ($invalid as [$name, $alphabet, $operation, $input]) {
    try {
        $codec = new SequentialStringId($alphabet);
        if ($operation === 'encode') $codec->encode($input);
        if ($operation === 'decode') $codec->decode($input);
    } catch (InvalidArgumentException | RuntimeException $error) {
        $output['rejections'][] = compact('name', 'alphabet', 'operation', 'input') + ['message' => $error->getMessage()];
        continue;
    }
    throw new RuntimeException('Expected rejection for ' . $name);
}
$database = json_decode(file_get_contents(__DIR__ . '/repository-data-events.json'), true, 512, JSON_THROW_ON_ERROR);
foreach ($database['events'] as $event) {
    $codec = new SequentialStringId($event['alphabet']);
    if ($codec->encodeVerified($event['value']) !== $event['sequential-string ID']) {
        throw new RuntimeException('Published record mismatch: ' . $event['label']);
    }
}
$output['published_records_verified'] = count($database['events']);
file_put_contents(__DIR__ . '/codec-vectors.json', json_encode($output, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR) . "\n");
echo 'Verified ' . count($cases) . ' round-trip vectors, ' . count($output['decode_aliases']) . ' leading-zero aliases, ' . count($invalid) . ' rejection vectors, and ' . count($database['events']) . " published records.\n";
