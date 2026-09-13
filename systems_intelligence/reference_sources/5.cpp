/*
 * configure_2.cpp
 *
 * Risk-free C++ replacement for the original numeric permutation generator.
 * It produces bounded, escaped cppdb source records suitable for use as stable
 * generated object handles in the planned CLI database.
 *
 * Build:
 *   c++ -std=c++17 -Wall -Wextra -pedantic -O2 configure_2.cpp -o configure_2
 *
 * Examples:
 *   ./configure_2 --help
 *   ./configure_2 --execute --limit 10000 --output x33.cppdb.txt
 *   ./configure_2 --execute --width 7 --all --allow-large
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

constexpr std::uint64_t kDefaultWidth = 7;
constexpr std::uint64_t kDefaultLimit = 10000;
constexpr std::uint64_t kUnapprovedRecordLimit = 100000;
constexpr std::uint64_t kMaxWidth = 18;
constexpr std::string_view kAlphabet = "0123456789";

struct Options {
    std::uint64_t width = kDefaultWidth;
    std::uint64_t start = 0;
    std::uint64_t limit = kDefaultLimit;
    bool all = false;
    bool allow_large = false;
    bool execute = false;
    bool overwrite = false;
    std::string output_path = "x33.cppdb.txt";
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

std::string make_numeric_value(std::uint64_t ordinal, std::uint64_t width) {
    std::string value(static_cast<std::size_t>(width), '0');

    for (std::uint64_t pos = 0; pos < width; ++pos) {
        const std::uint64_t reversed_index = width - 1 - pos;
        value[static_cast<std::size_t>(reversed_index)] =
            static_cast<char>('0' + (ordinal % 10));
        ordinal /= 10;
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
        << "  --width <n>           numeric width, default 7, maximum 18\n"
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
        << "  record<TAB>ordinal<TAB>role<TAB>width<TAB>fnv1a64<TAB>value\n";
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
        throw std::runtime_error("Width must be between 1 and 18.");
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
                  std::uint64_t total,
                  std::uint64_t count) {
    out << "# cppdb-configure-source v1\n";
    out << "# source_kind=configure_2\n";
    out << "# role=numeric_object_handle\n";
    out << "# record_mode=line\n";
    out << "# width=" << options.width << '\n';
    out << "# alphabet_name=decimal_digits\n";
    out << "# alphabet_size=" << kAlphabet.size() << '\n';
    out << "# expected_total=" << total << '\n';
    out << "# start=" << options.start << '\n';
    out << "# emitted_records=" << count << '\n';
    out << "# truncated=" << (options.start + count < total ? "true" : "false") << '\n';

    for (std::uint64_t i = 0; i < count; ++i) {
        const std::uint64_t ordinal = options.start + i;
        const std::string value = make_numeric_value(ordinal, options.width);
        out << "record\t"
            << ordinal << '\t'
            << "numeric_object_handle\t"
            << options.width << '\t'
            << hex64(fnv1a_64(value)) << '\t'
            << value << '\n';
    }
}

void print_plan(const Options& options, std::uint64_t total, std::uint64_t count) {
    std::cout
        << "configure_2 dry run\n"
        << "  output: " << options.output_path << '\n'
        << "  width: " << options.width << '\n'
        << "  alphabet: decimal_digits (" << kAlphabet.size() << " characters)\n"
        << "  expected total: " << total << '\n'
        << "  start: " << options.start << '\n'
        << "  records selected: " << count << '\n'
        << "  execute: false\n\n"
        << "Add --execute to write the cppdb numeric source-record output.\n";
}

int run(int argc, char** argv) {
    const Options options = parse_options(argc, argv);

    std::uint64_t total = 0;
    if (!safe_power(static_cast<std::uint64_t>(kAlphabet.size()), options.width, total)) {
        throw std::runtime_error("Combination count overflowed uint64_t.");
    }

    const std::uint64_t count = requested_count(options, total);
    validate_safety(options, count);

    if (!options.execute) {
        print_plan(options, total, count);
        return 0;
    }

    if (options.output_path == "-") {
        StreamSink sink(std::cout);
        Writer writer(sink);
        write_output(writer, options, total, count);
        return 0;
    }

    if (!options.overwrite && file_exists(options.output_path)) {
        throw std::runtime_error("Output file already exists. Use --overwrite to replace it: " +
                                 options.output_path);
    }

    FileSink sink(options.output_path);
    Writer writer(sink);
    write_output(writer, options, total, count);
    std::cout << "Wrote " << count << " configure_2 records to " << options.output_path << '\n';
    return 0;
}

} // namespace

int main(int argc, char** argv) {
    try {
        return run(argc, argv);
    } catch (const std::exception& ex) {
        std::cerr << "configure_2 error: " << ex.what() << '\n';
        return 1;
    }
}
