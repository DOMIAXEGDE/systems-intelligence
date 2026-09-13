<?php

declare(strict_types=1);

namespace MemoryMap;

use InvalidArgumentException;

/**
 * Tiny, dependency-free arithmetic for non-negative decimal integers.
 *
 * Sequential string IDs can be much larger than PHP_INT_MAX. Keeping the
 * representation decimal also means the value can be stored in JSON without
 * precision loss in PHP or JavaScript.
 */
final class BigNatural
{
    public static function normalize(string $number): string
    {
        if ($number === '' || preg_match('/^\d+$/D', $number) !== 1) {
            throw new InvalidArgumentException('Expected a non-negative decimal integer.');
        }

        $normalized = ltrim($number, '0');

        return $normalized === '' ? '0' : $normalized;
    }

    public static function compare(string $left, string $right): int
    {
        $left = self::normalize($left);
        $right = self::normalize($right);

        $lengthComparison = strlen($left) <=> strlen($right);
        if ($lengthComparison !== 0) {
            return $lengthComparison;
        }

        return strcmp($left, $right) <=> 0;
    }

    public static function add(string $left, string $right): string
    {
        $left = strrev(self::normalize($left));
        $right = strrev(self::normalize($right));
        $length = max(strlen($left), strlen($right));
        $carry = 0;
        $result = '';

        for ($index = 0; $index < $length; $index++) {
            $sum = ($index < strlen($left) ? (int) $left[$index] : 0)
                + ($index < strlen($right) ? (int) $right[$index] : 0)
                + $carry;
            $result .= (string) ($sum % 10);
            $carry = intdiv($sum, 10);
        }

        if ($carry > 0) {
            $result .= (string) $carry;
        }

        return strrev($result);
    }

    public static function addSmall(string $number, int $addend): string
    {
        if ($addend < 0) {
            throw new InvalidArgumentException('The addend must be non-negative.');
        }

        return self::add($number, (string) $addend);
    }

    public static function subtract(string $left, string $right): string
    {
        $left = self::normalize($left);
        $right = self::normalize($right);

        if (self::compare($left, $right) < 0) {
            throw new InvalidArgumentException('BigNatural subtraction cannot produce a negative value.');
        }

        $left = strrev($left);
        $right = strrev($right);
        $borrow = 0;
        $result = '';

        for ($index = 0, $length = strlen($left); $index < $length; $index++) {
            $digit = (int) $left[$index] - $borrow;
            $subtrahend = $index < strlen($right) ? (int) $right[$index] : 0;
            if ($digit < $subtrahend) {
                $digit += 10;
                $borrow = 1;
            } else {
                $borrow = 0;
            }
            $result .= (string) ($digit - $subtrahend);
        }

        return self::normalize(strrev($result));
    }

    public static function multiplySmall(string $number, int $multiplier): string
    {
        $number = self::normalize($number);
        if ($multiplier < 0) {
            throw new InvalidArgumentException('The multiplier must be non-negative.');
        }
        if ($number === '0' || $multiplier === 0) {
            return '0';
        }

        $number = strrev($number);
        $carry = 0;
        $result = '';

        for ($index = 0, $length = strlen($number); $index < $length; $index++) {
            $product = ((int) $number[$index] * $multiplier) + $carry;
            $result .= (string) ($product % 10);
            $carry = intdiv($product, 10);
        }

        while ($carry > 0) {
            $result .= (string) ($carry % 10);
            $carry = intdiv($carry, 10);
        }

        return strrev($result);
    }

    /** @return array{0: string, 1: int} quotient and remainder */
    public static function divideSmall(string $number, int $divisor): array
    {
        $number = self::normalize($number);
        if ($divisor < 1) {
            throw new InvalidArgumentException('The divisor must be positive.');
        }

        $remainder = 0;
        $quotient = '';

        for ($index = 0, $length = strlen($number); $index < $length; $index++) {
            $partial = ($remainder * 10) + (int) $number[$index];
            $quotient .= (string) intdiv($partial, $divisor);
            $remainder = $partial % $divisor;
        }

        return [self::normalize($quotient), $remainder];
    }
}
