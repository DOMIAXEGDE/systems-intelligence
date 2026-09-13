<?php

declare(strict_types=1);

namespace MemoryMap;

use InvalidArgumentException;
use RuntimeException;

/**
 * Reversible shortlex ranking of strings over a user-defined alphabet.
 *
 * ID 0 is the empty string. Non-empty strings are ordered first by length,
 * then by their alphabetic rank. For alphabet "ab": a=1, b=2, aa=3, ...
 */
final class SequentialStringId
{
    public const MAX_STRING_SYMBOLS = 4096;
    public const MAX_ALPHABET_SYMBOLS = 1024;

    /** @var list<string> */
    private array $symbols;

    /** @var array<string, int> */
    private array $indices;

    private int $base;

    public function __construct(string $alphabet)
    {
        self::assertUtf8($alphabet, 'alphabet');
        $symbols = mb_str_split($alphabet);
        $symbolCount = count($symbols);

        if ($symbolCount < 2) {
            throw new InvalidArgumentException('Define an alphabet with at least two symbols.');
        }
        if ($symbolCount > self::MAX_ALPHABET_SYMBOLS) {
            throw new InvalidArgumentException(sprintf(
                'The alphabet is too large; use at most %d symbols.',
                self::MAX_ALPHABET_SYMBOLS,
            ));
        }

        $indices = [];
        foreach ($symbols as $index => $symbol) {
            if (array_key_exists($symbol, $indices)) {
                throw new InvalidArgumentException(sprintf(
                    'Alphabet symbol %s occurs more than once.',
                    self::describeSymbol($symbol),
                ));
            }
            $indices[$symbol] = $index;
        }

        $this->symbols = $symbols;
        $this->indices = $indices;
        $this->base = $symbolCount;
    }

    public function alphabetSize(): int
    {
        return $this->base;
    }

    public function encode(string $input): string
    {
        self::assertUtf8($input, 'string-input');
        $inputSymbols = mb_str_split($input);
        $length = count($inputSymbols);

        if ($length > self::MAX_STRING_SYMBOLS) {
            throw new InvalidArgumentException(sprintf(
                'Use a smaller string-input (at most %d symbols).',
                self::MAX_STRING_SYMBOLS,
            ));
        }
        if ($length === 0) {
            return '0';
        }

        $shorterStringCount = '0';
        $stringsAtLength = '1';
        for ($size = 1; $size < $length; $size++) {
            $stringsAtLength = BigNatural::multiplySmall($stringsAtLength, $this->base);
            $shorterStringCount = BigNatural::add($shorterStringCount, $stringsAtLength);
        }

        $rank = '0';
        foreach ($inputSymbols as $position => $symbol) {
            if (!array_key_exists($symbol, $this->indices)) {
                throw new InvalidArgumentException(sprintf(
                    'Symbol %s at position %d is not present in the input alphabet.',
                    self::describeSymbol($symbol),
                    $position + 1,
                ));
            }

            $rank = BigNatural::multiplySmall($rank, $this->base);
            $rank = BigNatural::addSmall($rank, $this->indices[$symbol]);
        }

        return BigNatural::addSmall(BigNatural::add($shorterStringCount, $rank), 1);
    }

    public function decode(string $id): string
    {
        $id = BigNatural::normalize($id);
        if ($id === '0') {
            return '';
        }

        $remaining = $id;
        $length = 1;
        $bucketSize = (string) $this->base;

        while (BigNatural::compare($remaining, $bucketSize) > 0) {
            $remaining = BigNatural::subtract($remaining, $bucketSize);
            $length++;
            if ($length > self::MAX_STRING_SYMBOLS) {
                throw new RuntimeException('The sequential-string ID exceeds the supported context size.');
            }
            $bucketSize = BigNatural::multiplySmall($bucketSize, $this->base);
        }

        $offset = BigNatural::subtract($remaining, '1');
        $decoded = array_fill(0, $length, $this->symbols[0]);

        for ($position = $length - 1; $position >= 0; $position--) {
            [$offset, $remainder] = BigNatural::divideSmall($offset, $this->base);
            $decoded[$position] = $this->symbols[$remainder];
        }

        return implode('', $decoded);
    }

    public function encodeVerified(string $input): string
    {
        $id = $this->encode($input);
        if ($this->decode($id) !== $input) {
            throw new RuntimeException(
                'The sequential-string ID did not reverse exactly. Use a smaller string-input.',
            );
        }

        return $id;
    }

    private static function assertUtf8(string $value, string $field): void
    {
        if (preg_match('//u', $value) !== 1) {
            throw new InvalidArgumentException(sprintf('%s must be valid UTF-8.', $field));
        }
    }

    private static function describeSymbol(string $symbol): string
    {
        return match ($symbol) {
            " " => 'SPACE',
            "\n" => 'LINE FEED',
            "\r" => 'CARRIAGE RETURN',
            "\t" => 'TAB',
            default => sprintf('“%s”', $symbol),
        };
    }
}
