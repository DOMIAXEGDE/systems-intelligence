/*
 * configure_1.cpp
 *
 * Risk-free C++ replacement for the original broad-character permutation
 * generator. It emits a stable cppdb source-record format that can represent
 * embedded whitespace and newlines without corrupting database ingestion.
 *
 * Build:
 *   c++ -std=c++17 -Wall -Wextra -pedantic -O2 configure_1.cpp -o configure_1
 *
 * Examples:
 *   ./configure_1 --help
 *   ./configure_1 --execute --limit 1000 --output system_safe.cppdb.txt
 *   ./configure_1 --execute --alphabet safe --width 3 --all --allow-large
 */

#include <cctype>
#include <cstdio>
#include <cstdint>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

namespace {

constexpr std::uint64_t kDefaultWidth = 4;
constexpr std::uint64_t kDefaultLimit = 10000;
constexpr std::uint64_t kUnapprovedRecordLimit = 100000;
constexpr std::uint64_t kMaxWidth = 16;

struct Options {
    std::uint64_t width = kDefaultWidth;
    std::uint64_t start = 0;
    std::uint64_t limit = kDefaultLimit;
    bool all = false;
    bool allow_large = false;
    bool execute = false;
    bool overwrite = false;
    std::string alphabet_name = "original";
    std::string output_path = "system_safe.cppdb.txt";
};

class OutputSink {
public:
    virtual ~OutputSink() = default;
    virtual void write(std::string_view value) = 0;
};

class StreamSink final : public OutputSink {
public:
    explicit StreamSink(std::ostream& stream) : stream_(stream) {}

    void write(std::string_view value) override {
        stream_.write(value.data(), static_cast<std::streamsize>(value.size()));
        if (!stream_) {
            throw std::runtime_error("Failed while writing to stream.");
        }
    }

private:
    std::ostream& stream_;
};

class FileSink final : public OutputSink {
public:
    explicit FileSink(const std::string& path) : file_(std::fopen(path.c_str(), "wb")) {
        if (file_ == nullptr) {
            throw std::runtime_error("Unable to open output file: " + path);
        }
    }

    ~FileSink() override {
        if (file_ != nullptr) {
            std::fclose(file_);
        }
    }

    FileSink(const FileSink&) = delete;
    FileSink& operator=(const FileSink&) = delete;

    void write(std::string_view value) override {
        if (!value.empty() &&
            std::fwrite(value.data(), 1, value.size(), file_) != value.size()) {
            throw std::runtime_error("Failed while writing output file.");
        }
    }

private:
    std::FILE* file_;
};

class Writer {
public:
    explicit Writer(OutputSink& sink) : sink_(sink) {}

    Writer& operator<<(char value) {
        sink_.write(std::string_view(&value, 1));
        return *this;
    }

    Writer& operator<<(std::string_view value) {
        sink_.write(value);
        return *this;
    }

    Writer& operator<<(const std::string& value) {
        sink_.write(value);
        return *this;
    }

    Writer& operator<<(const char* value) {
        sink_.write(value == nullptr ? std::string_view{} : std::string_view(value));
        return *this;
    }

    template <typename T>
    Writer& operator<<(const T& value) {
        std::ostringstream out;
        out << value;
        sink_.write(out.str());
        return *this;
    }

private:
    OutputSink& sink_;
};

std::string to_lower(std::string value) {
    for (char& c : value) {
        c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    }
    return value;
}

bool parse_u64(std::string_view text, std::uint64_t& out) {
    if (text.empty()) {
        return false;
    }

    std::uint64_t value = 0;
    for (char c : text) {
        if (!std::isdigit(static_cast<unsigned char>(c))) {
            return false;
        }
        const std::uint64_t digit = static_cast<std::uint64_t>(c - '0');
        if (value > (std::numeric_limits<std::uint64_t>::max() - digit) / 10) {
            return false;
        }
        value = value * 10 + digit;
    }

    out = value;
    return true;
}

std::uint64_t require_u64(const std::vector<std::string>& args, std::size_t& i) {
    if (i + 1 >= args.size()) {
        throw std::runtime_error("Missing value for " + args[i]);
    }

    std::uint64_t value = 0;
    if (!parse_u64(args[++i], value)) {
        throw std::runtime_error("Invalid unsigned integer for " + args[i - 1] + ": " + args[i]);
    }
    return value;
}

std::string require_string(const std::vector<std::string>& args, std::size_t& i) {
    if (i + 1 >= args.size()) {
        throw std::runtime_error("Missing value for " + args[i]);
    }
    return args[++i];
}

bool safe_power(std::uint64_t base, std::uint64_t exp, std::uint64_t& out) {
    if (base == 0) {
        return false;
    }

    std::uint64_t value = 1;
    for (std::uint64_t i = 0; i < exp; ++i) {
        if (value > std::numeric_limits<std::uint64_t>::max() / base) {
            return false;
        }
        value *= base;
    }

    out = value;
    return true;
}

std::string original_alphabet() {
    std::string alphabet;
    for (char c = 'a'; c <= 'z'; ++c) {
        alphabet.push_back(c);
    }
    for (char c = 'A'; c <= 'Z'; ++c) {
        alphabet.push_back(c);
    }
    for (char c = '0'; c <= '9'; ++c) {
        alphabet.push_back(c);
    }
    alphabet.push_back(' ');
    alphabet.push_back('\n');
    return alphabet;
}

std::string alphabet_for_name(const std::string& name) {
    const std::string normalized = to_lower(name);
    if (normalized == "original") {
        return original_alphabet();
    }
    if (normalized == "safe") {
        return "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_";
    }
    if (normalized == "digits") {
        return "0123456789";
    }
    if (normalized == "lower") {
        return "abcdefghijklmnopqrstuvwxyz";
    }
    throw std::runtime_error("Unknown alphabet: " + name);
}

std::string escape_field(std::string_view value) {
    std::ostringstream out;
    out << std::hex << std::uppercase << std::setfill('0');

    for (unsigned char c : value) {
        if (c == '\\') {
            out << "\\\\";
        } else if (c == '\n') {
            out << "\\n";
        } else if (c == '\r') {
            out << "\\r";
        } else if (c == '\t') {
            out << "\\t";
        } else if (c < 32 || c == 127) {
            out << "\\x" << std::setw(2) << static_cast<unsigned int>(c);
        } else {
            out << static_cast<char>(c);
        }
    }

    return out.str();
}

std::uint64_t fnv1a_64(std::string_view value) {
    std::uint64_t hash = 14695981039346656037ull;
    for (unsigned char c : value) {
        hash ^= static_cast<std::uint64_t>(c);
        hash *= 1099511628211ull;
    }
    return hash;
}

std::string hex64(std::uint64_t value) {
    std::ostringstream out;
    out << std::hex << std::nouppercase << std::setfill('0') << std::setw(16) << value;
    return out.str();
}

std::string make_record_value(std::uint64_t ordinal, std::uint64_t width, std::string_view alphabet) {
    std::string value(static_cast<std::size_t>(width), '\0');
    const std::uint64_t base = static_cast<std::uint64_t>(alphabet.size());

    for (std::uint64_t pos = 0; pos < width; ++pos) {
        const std::uint64_t reversed_index = width - 1 - pos;
        const std::uint64_t alphabet_index = ordinal % base;
        value[static_cast<std::size_t>(reversed_index)] = alphabet[static_cast<std::size_t>(alphabet_index)];
        ordinal /= base;
    }

    return value;
}

bool file_exists(const std::string& path) {
    std::FILE* file = std::fopen(path.c_str(), "rb");
    if (file == nullptr) {
        return false;
    }
    std::fclose(file);
    return true;
}

void print_help(const char* executable) {
    std::cout
        << "Usage:\n"
        << "  " << executable << " --execute [options]\n"
        << "  " << executable << " [options]              # dry-run plan only\n\n"
        << "Options:\n"
        << "  --width <n>           record width, default 4, maximum 16\n"
        << "  --alphabet <name>     original|safe|digits|lower, default original\n"
        << "  --start <n>           first ordinal to emit, default 0\n"
        << "  --limit <n>           maximum records to emit, default 10000\n"
        << "  --all                 emit all records after --start\n"
        << "  --allow-large         allow more than 100000 records\n"
        << "  --output <path|- >    output file, or - for stdout\n"
        << "  --overwrite           allow replacing an existing output file\n"
        << "  --execute             actually write output\n"
        << "  --help                show this help\n\n"
        << "Output format:\n"
        << "  comment metadata lines beginning with '#'\n"
        << "  record<TAB>ordinal<TAB>role<TAB>width<TAB>fnv1a64<TAB>escaped_value\n";
}

Options parse_options(int argc, char** argv) {
    Options options;
    std::vector<std::string> args(argv, argv + argc);

    for (std::size_t i = 1; i < args.size(); ++i) {
        const std::string& arg = args[i];
        if (arg == "--help" || arg == "-h") {
            print_help(argv[0]);
            std::exit(0);
        } else if (arg == "--width" || arg == "-w") {
            options.width = require_u64(args, i);
        } else if (arg == "--alphabet" || arg == "-a") {
            options.alphabet_name = require_string(args, i);
        } else if (arg == "--start") {
            options.start = require_u64(args, i);
        } else if (arg == "--limit") {
            options.limit = require_u64(args, i);
        } else if (arg == "--all") {
            options.all = true;
        } else if (arg == "--allow-large") {
            options.allow_large = true;
        } else if (arg == "--output" || arg == "-o") {
            options.output_path = require_string(args, i);
        } else if (arg == "--overwrite") {
            options.overwrite = true;
        } else if (arg == "--execute") {
            options.execute = true;
        } else {
            throw std::runtime_error("Unknown option: " + arg);
        }
    }

    if (options.width == 0 || options.width > kMaxWidth) {
        throw std::runtime_error("Width must be between 1 and 16.");
    }

    return options;
}

std::uint64_t requested_count(const Options& options, std::uint64_t total) {
    if (options.start >= total) {
        return 0;
    }

    const std::uint64_t remaining = total - options.start;
    return options.all || options.limit > remaining ? remaining : options.limit;
}

void validate_safety(const Options& options, std::uint64_t count) {
    if (!options.allow_large && count > kUnapprovedRecordLimit) {
        throw std::runtime_error(
            "Refusing to emit more than 100000 records without --allow-large.");
    }
}

void write_output(Writer& out,
                  const Options& options,
                  const std::string& alphabet,
                  std::uint64_t total,
                  std::uint64_t count) {
    out << "# cppdb-configure-source v1\n";
    out << "# source_kind=configure_1\n";
    out << "# role=generated_symbol_candidate\n";
    out << "# record_mode=escaped_line\n";
    out << "# width=" << options.width << '\n';
    out << "# alphabet_name=" << options.alphabet_name << '\n';
    out << "# alphabet_size=" << alphabet.size() << '\n';
    out << "# expected_total=" << total << '\n';
    out << "# start=" << options.start << '\n';
    out << "# emitted_records=" << count << '\n';
    out << "# truncated=" << (options.start + count < total ? "true" : "false") << '\n';

    for (std::uint64_t i = 0; i < count; ++i) {
        const std::uint64_t ordinal = options.start + i;
        const std::string value = make_record_value(ordinal, options.width, alphabet);
        out << "record\t"
            << ordinal << '\t'
            << "generated_symbol_candidate\t"
            << options.width << '\t'
            << hex64(fnv1a_64(value)) << '\t'
            << escape_field(value) << '\n';
    }
}

void print_plan(const Options& options,
                const std::string& alphabet,
                std::uint64_t total,
                std::uint64_t count) {
    std::cout
        << "configure_1 dry run\n"
        << "  output: " << options.output_path << '\n'
        << "  width: " << options.width << '\n'
        << "  alphabet: " << options.alphabet_name << " (" << alphabet.size() << " characters)\n"
        << "  expected total: " << total << '\n'
        << "  start: " << options.start << '\n'
        << "  records selected: " << count << '\n'
        << "  execute: false\n\n"
        << "Add --execute to write the escaped cppdb source-record output.\n";
}

int run(int argc, char** argv) {
    const Options options = parse_options(argc, argv);
    const std::string alphabet = alphabet_for_name(options.alphabet_name);

    std::uint64_t total = 0;
    if (!safe_power(static_cast<std::uint64_t>(alphabet.size()), options.width, total)) {
        throw std::runtime_error("Combination count overflowed uint64_t.");
    }

    const std::uint64_t count = requested_count(options, total);
    validate_safety(options, count);

    if (!options.execute) {
        print_plan(options, alphabet, total, count);
        return 0;
    }

    if (options.output_path == "-") {
        StreamSink sink(std::cout);
        Writer writer(sink);
        write_output(writer, options, alphabet, total, count);
        return 0;
    }

    if (!options.overwrite && file_exists(options.output_path)) {
        throw std::runtime_error("Output file already exists. Use --overwrite to replace it: " +
                                 options.output_path);
    }

    FileSink sink(options.output_path);
    Writer writer(sink);
    write_output(writer, options, alphabet, total, count);
    std::cout << "Wrote " << count << " configure_1 records to " << options.output_path << '\n';
    return 0;
}

} // namespace

int main(int argc, char** argv) {
    try {
        return run(argc, argv);
    } catch (const std::exception& ex) {
        std::cerr << "configure_1 error: " << ex.what() << '\n';
        return 1;
    }
}
