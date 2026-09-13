/*
 * configure_3.cpp
 *
 * Risk-free C++ generative fabric for cppdb. This replaces the C configurator
 * with explicit object specs, bounded execution, dry-run planning, escaped
 * source records, and config/direct/menu input paths.
 *
 * Build:
 *   c++ -std=c++17 -Wall -Wextra -pedantic -O2 configure_3.cpp -o configure_3
 *
 * Examples:
 *   ./configure_3 --sample-config x33.fabric
 *   ./configure_3 --config x33.fabric
 *   ./configure_3 --config x33.fabric --execute --overwrite
 *   ./configure_3 --execute --object-name x33 --input-value 01 --input-width 3
 */

#include <algorithm>
#include <cctype>
#include <cstdio>
#include <cstdint>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <map>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

namespace {

constexpr std::uint64_t kDefaultLimit = 10000;
constexpr std::uint64_t kUnapprovedRecordLimit = 100000;
constexpr std::uint64_t kMaxCartesianWidth = 64;
constexpr std::uint64_t kMaxRepeatCount = 1000000;
constexpr std::size_t kMaxTextLength = 4096;

enum class Flow {
    cartesian,
    literal,
    repeat,
    reverse
};

struct ObjectSpec {
    bool touched = false;
    std::string object_name = "x33";
    std::string input_name = "x1";
    std::string input_value = "0123456789";
    std::uint64_t input_width = 7;
    std::string flow_name = "x15";
    Flow flow = Flow::cartesian;
    std::string output_name = "x31";
    std::string output_target = "x33.cppdb.txt";
    std::string separator = "\n";
    std::string prefix;
    std::string suffix;
};

struct Options {
    bool execute = false;
    bool allow_large = false;
    bool all = false;
    bool overwrite = false;
    bool menu = false;
    std::uint64_t start = 0;
    std::uint64_t limit = kDefaultLimit;
    std::string config_path;
    std::string sample_config_path;
};

struct Program {
    Options options;
    ObjectSpec direct_spec;
    bool has_direct_spec = false;
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
    FileSink(const std::string& path, const char* mode) : file_(std::fopen(path.c_str(), mode)) {
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

class InputFile {
public:
    explicit InputFile(const std::string& path) : file_(std::fopen(path.c_str(), "rb")) {
        if (file_ == nullptr) {
            throw std::runtime_error("Unable to open config file: " + path);
        }
    }

    ~InputFile() {
        if (file_ != nullptr) {
            std::fclose(file_);
        }
    }

    InputFile(const InputFile&) = delete;
    InputFile& operator=(const InputFile&) = delete;

    bool read_line(std::string& line) {
        char buffer[8192];
        line.clear();

        for (;;) {
            if (std::fgets(buffer, sizeof(buffer), file_) == nullptr) {
                if (std::ferror(file_) != 0) {
                    throw std::runtime_error("Failed while reading config file.");
                }
                return !line.empty();
            }

            line += buffer;
            if (!line.empty() && line.back() == '\n') {
                break;
            }

            const std::size_t chunk_size = std::char_traits<char>::length(buffer);
            if (chunk_size + 1 < sizeof(buffer)) {
                break;
            }
        }

        while (!line.empty() && (line.back() == '\n' || line.back() == '\r')) {
            line.pop_back();
        }

        return true;
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

std::string trim(std::string value) {
    while (!value.empty() && std::isspace(static_cast<unsigned char>(value.front()))) {
        value.erase(value.begin());
    }
    while (!value.empty() && std::isspace(static_cast<unsigned char>(value.back()))) {
        value.pop_back();
    }
    return value;
}

bool equals_ignore_case(std::string_view lhs, std::string_view rhs) {
    if (lhs.size() != rhs.size()) {
        return false;
    }

    for (std::size_t i = 0; i < lhs.size(); ++i) {
        if (std::tolower(static_cast<unsigned char>(lhs[i])) !=
            std::tolower(static_cast<unsigned char>(rhs[i]))) {
            return false;
        }
    }

    return true;
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

std::string unescape_text(std::string_view value) {
    std::string out;
    out.reserve(value.size());

    for (std::size_t i = 0; i < value.size(); ++i) {
        char c = value[i];
        if (c != '\\' || i + 1 >= value.size()) {
            out.push_back(c);
            continue;
        }

        const char next = value[++i];
        if (next == 'n') {
            out.push_back('\n');
        } else if (next == 't') {
            out.push_back('\t');
        } else if (next == 'r') {
            out.push_back('\r');
        } else if (next == '\\') {
            out.push_back('\\');
        } else {
            out.push_back(next);
        }
    }

    return out;
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

std::string flow_name(Flow flow) {
    if (flow == Flow::cartesian) {
        return "cartesian";
    }
    if (flow == Flow::literal) {
        return "literal";
    }
    if (flow == Flow::repeat) {
        return "repeat";
    }
    return "reverse";
}

Flow parse_flow(std::string_view value) {
    const std::string normalized = to_lower(std::string(value));
    if (normalized == "cartesian" || normalized == "product" || normalized == "combinations") {
        return Flow::cartesian;
    }
    if (normalized == "literal" || normalized == "echo") {
        return Flow::literal;
    }
    if (normalized == "repeat") {
        return Flow::repeat;
    }
    if (normalized == "reverse") {
        return Flow::reverse;
    }
    throw std::runtime_error("Unknown flow_type: " + std::string(value));
}

std::string make_cartesian_value(std::uint64_t ordinal,
                                 std::uint64_t width,
                                 std::string_view alphabet) {
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

std::string make_payload(const ObjectSpec& spec, std::uint64_t ordinal) {
    if (spec.flow == Flow::cartesian) {
        return make_cartesian_value(ordinal, spec.input_width, spec.input_value);
    }
    if (spec.flow == Flow::literal) {
        return spec.input_value;
    }
    if (spec.flow == Flow::repeat) {
        return spec.input_value;
    }

    std::string reversed = spec.input_value;
    std::reverse(reversed.begin(), reversed.end());
    return reversed;
}

std::uint64_t expected_total(const ObjectSpec& spec) {
    if (spec.flow == Flow::cartesian) {
        std::uint64_t total = 0;
        if (!safe_power(static_cast<std::uint64_t>(spec.input_value.size()),
                        spec.input_width,
                        total)) {
            throw std::runtime_error("Combination count overflowed uint64_t for " +
                                     spec.object_name);
        }
        return total;
    }

    if (spec.flow == Flow::repeat) {
        return spec.input_width;
    }

    return 1;
}

std::uint64_t requested_count(const Options& options, std::uint64_t total) {
    if (options.start >= total) {
        return 0;
    }

    const std::uint64_t remaining = total - options.start;
    return options.all || options.limit > remaining ? remaining : options.limit;
}

bool file_exists(const std::string& path) {
    std::FILE* file = std::fopen(path.c_str(), "rb");
    if (file == nullptr) {
        return false;
    }
    std::fclose(file);
    return true;
}

void validate_text_length(const std::string& label, const std::string& value) {
    if (value.size() > kMaxTextLength) {
        throw std::runtime_error(label + " is too large; maximum length is 4096 bytes.");
    }
}

void validate_spec(const ObjectSpec& spec) {
    if (spec.object_name.empty() || spec.input_name.empty() || spec.flow_name.empty() ||
        spec.output_name.empty() || spec.output_target.empty()) {
        throw std::runtime_error("Object spec has an empty required name or output target.");
    }

    validate_text_length("input_value", spec.input_value);
    validate_text_length("prefix", spec.prefix);
    validate_text_length("suffix", spec.suffix);
    validate_text_length("separator", spec.separator);

    if (spec.input_width == 0) {
        throw std::runtime_error("input_width must be greater than zero for " + spec.object_name);
    }

    if (spec.flow == Flow::cartesian) {
        if (spec.input_value.empty()) {
            throw std::runtime_error("cartesian input_value cannot be empty for " + spec.object_name);
        }
        if (spec.input_width > kMaxCartesianWidth) {
            throw std::runtime_error("cartesian input_width exceeds 64 for " + spec.object_name);
        }
        (void)expected_total(spec);
    }

    if (spec.flow == Flow::repeat && spec.input_width > kMaxRepeatCount) {
        throw std::runtime_error("repeat input_width exceeds 1000000 for " + spec.object_name);
    }
}

void validate_safety(const Options& options,
                     const ObjectSpec& spec,
                     std::uint64_t count) {
    if (!options.allow_large && count > kUnapprovedRecordLimit) {
        throw std::runtime_error("Refusing to emit more than 100000 records for " +
                                 spec.object_name + " without --allow-large.");
    }
}

void set_spec_value(ObjectSpec& spec, std::string key, std::string value) {
    key = to_lower(trim(key));
    value = trim(value);
    spec.touched = true;

    if (key == "object" || key == "object_name" || key == "name") {
        spec.object_name = value;
    } else if (key == "input_name") {
        spec.input_name = value;
    } else if (key == "input" || key == "input_value" || key == "alphabet") {
        spec.input_value = unescape_text(value);
    } else if (key == "input_width" || key == "width" || key == "length" || key == "depth") {
        if (!parse_u64(value, spec.input_width)) {
            throw std::runtime_error("Invalid input_width: " + value);
        }
    } else if (key == "flow_name") {
        spec.flow_name = value;
    } else if (key == "flow" || key == "flow_type" || key == "processing_flow") {
        spec.flow = parse_flow(value);
    } else if (key == "output_name") {
        spec.output_name = value;
    } else if (key == "output" || key == "output_target" || key == "file") {
        spec.output_target = value;
    } else if (key == "separator" || key == "sep") {
        spec.separator = unescape_text(value);
    } else if (key == "prefix") {
        spec.prefix = unescape_text(value);
    } else if (key == "suffix") {
        spec.suffix = unescape_text(value);
    } else {
        throw std::runtime_error("Unknown config key: " + key);
    }
}

bool parse_config_line(std::string line, std::string& key, std::string& value) {
    const std::size_t comment = line.find('#');
    if (comment != std::string::npos) {
        line.erase(comment);
    }

    line = trim(line);
    if (line.empty()) {
        return false;
    }

    const std::size_t equals = line.find('=');
    if (equals != std::string::npos) {
        key = trim(line.substr(0, equals));
        value = trim(line.substr(equals + 1));
        return !key.empty();
    }

    std::istringstream in(line);
    in >> key;
    std::getline(in, value);
    value = trim(value);
    return !key.empty();
}

std::vector<ObjectSpec> load_config(const std::string& path) {
    InputFile in(path);
    std::vector<ObjectSpec> specs;
    ObjectSpec current;
    std::string line;
    std::uint64_t line_number = 0;

    while (in.read_line(line)) {
        ++line_number;

        std::string key;
        std::string value;
        if (!parse_config_line(line, key, value)) {
            continue;
        }

        if (equals_ignore_case(key, "end")) {
            if (current.touched) {
                validate_spec(current);
                specs.push_back(current);
                current = ObjectSpec{};
            }
            continue;
        }

        if ((equals_ignore_case(key, "object") || equals_ignore_case(key, "object_name")) &&
            current.touched) {
            validate_spec(current);
            specs.push_back(current);
            current = ObjectSpec{};
        }

        try {
            set_spec_value(current, key, value);
        } catch (const std::exception& ex) {
            std::ostringstream message;
            message << "Config line " << line_number << ": " << ex.what();
            throw std::runtime_error(message.str());
        }
    }

    if (current.touched) {
        validate_spec(current);
        specs.push_back(current);
    }

    if (specs.empty()) {
        throw std::runtime_error("Config file contains no object specs: " + path);
    }

    return specs;
}

void write_sample_config(const std::string& path, bool overwrite) {
    if (!overwrite && file_exists(path)) {
        throw std::runtime_error("Sample config already exists. Use --overwrite to replace it: " + path);
    }

    FileSink sink(path, "wb");
    Writer out(sink);
    out
        << "# cppdb configure_3 fabric config\n"
        << "object_name=x33\n"
        << "input_name=x1\n"
        << "input_value=0123456789\n"
        << "input_width=3\n"
        << "flow_name=x15\n"
        << "flow_type=cartesian\n"
        << "output_name=x31\n"
        << "output_target=x33.cppdb.txt\n"
        << "separator=\\n\n"
        << "prefix=\n"
        << "suffix=\n"
        << "end\n"
        << "\n"
        << "object_name=asset_alias_seed\n"
        << "input_name=alias_text\n"
        << "input_value=PlayerController\n"
        << "input_width=1\n"
        << "flow_name=reverse_alias_demo\n"
        << "flow_type=reverse\n"
        << "output_name=stdout_preview\n"
        << "output_target=stdout\n"
        << "separator=\\n\n"
        << "end\n";
}

std::string prompt_string(const std::string& label, const std::string& default_value) {
    std::cout << label << " [" << escape_field(default_value) << "]: ";
    std::string value;
    std::getline(std::cin, value);
    return value.empty() ? default_value : value;
}

std::uint64_t prompt_u64(const std::string& label, std::uint64_t default_value) {
    for (;;) {
        std::cout << label << " [" << default_value << "]: ";
        std::string value;
        std::getline(std::cin, value);
        if (value.empty()) {
            return default_value;
        }

        std::uint64_t parsed = 0;
        if (parse_u64(value, parsed)) {
            return parsed;
        }

        std::cout << "Please enter a non-negative integer.\n";
    }
}

ObjectSpec prompt_spec() {
    ObjectSpec spec;
    spec.object_name = prompt_string("object_name", spec.object_name);
    spec.input_name = prompt_string("input_name", spec.input_name);
    spec.input_value = unescape_text(prompt_string("input_value", spec.input_value));
    spec.input_width = prompt_u64("input_width", spec.input_width);
    spec.flow_name = prompt_string("flow_name", spec.flow_name);
    spec.flow = parse_flow(prompt_string("flow_type cartesian|literal|repeat|reverse", "cartesian"));
    spec.output_name = prompt_string("output_name", spec.output_name);
    spec.output_target = prompt_string("output_target file|stdout|-", spec.output_target);
    spec.separator = unescape_text(prompt_string("separator", "\\n"));
    spec.prefix = unescape_text(prompt_string("prefix", ""));
    spec.suffix = unescape_text(prompt_string("suffix", ""));
    spec.touched = true;
    validate_spec(spec);
    return spec;
}

void print_help(const char* executable) {
    std::cout
        << "Usage:\n"
        << "  " << executable << " --menu\n"
        << "  " << executable << " --config <path> [--execute]\n"
        << "  " << executable << " --sample-config <path>\n"
        << "  " << executable << " [object flags] [--execute]\n\n"
        << "Object flags:\n"
        << "  --object-name <name>       runtime object name\n"
        << "  --input-name <name>        runtime input name\n"
        << "  --input-value <value>      payload/alphabet, supports \\\\n, \\\\t, \\\\r\n"
        << "  --input-width <n>          width/count/depth\n"
        << "  --flow-name <name>         runtime processing-flow name\n"
        << "  --flow-type <type>         cartesian|literal|repeat|reverse\n"
        << "  --output-name <name>       runtime output name\n"
        << "  --output-target <target>   file path, stdout, or -\n"
        << "  --separator <text>         stored as metadata, supports escapes\n"
        << "  --prefix <text>            prepended to rendered value\n"
        << "  --suffix <text>            appended to rendered value\n\n"
        << "Execution controls:\n"
        << "  --start <n>                first ordinal, default 0\n"
        << "  --limit <n>                maximum records, default 10000\n"
        << "  --all                      emit all records after --start\n"
        << "  --allow-large              allow more than 100000 records\n"
        << "  --overwrite                allow replacing output files\n"
        << "  --execute                  actually write output\n\n"
        << "Aliases:\n"
        << "  -n <name>  -i <value>  -w <n>  -f <type>  -o <target>\n";
}

Program parse_program(int argc, char** argv) {
    Program program;
    std::vector<std::string> args(argv, argv + argc);

    for (std::size_t i = 1; i < args.size(); ++i) {
        const std::string& arg = args[i];

        if (arg == "--help" || arg == "-h") {
            print_help(argv[0]);
            std::exit(0);
        } else if (arg == "--execute") {
            program.options.execute = true;
        } else if (arg == "--allow-large") {
            program.options.allow_large = true;
        } else if (arg == "--all") {
            program.options.all = true;
        } else if (arg == "--overwrite") {
            program.options.overwrite = true;
        } else if (arg == "--menu" || arg == "--ui") {
            program.options.menu = true;
        } else if (arg == "--start") {
            program.options.start = require_u64(args, i);
        } else if (arg == "--limit") {
            program.options.limit = require_u64(args, i);
        } else if (arg == "--config") {
            program.options.config_path = require_string(args, i);
        } else if (arg == "--sample-config") {
            program.options.sample_config_path = require_string(args, i);
        } else if (arg == "--object-name" || arg == "-n") {
            set_spec_value(program.direct_spec, "object_name", require_string(args, i));
            program.has_direct_spec = true;
        } else if (arg == "--input-name") {
            set_spec_value(program.direct_spec, "input_name", require_string(args, i));
            program.has_direct_spec = true;
        } else if (arg == "--input-value" || arg == "-i") {
            set_spec_value(program.direct_spec, "input_value", require_string(args, i));
            program.has_direct_spec = true;
        } else if (arg == "--input-width" || arg == "-w") {
            set_spec_value(program.direct_spec, "input_width", require_string(args, i));
            program.has_direct_spec = true;
        } else if (arg == "--flow-name") {
            set_spec_value(program.direct_spec, "flow_name", require_string(args, i));
            program.has_direct_spec = true;
        } else if (arg == "--flow-type" || arg == "-f") {
            set_spec_value(program.direct_spec, "flow_type", require_string(args, i));
            program.has_direct_spec = true;
        } else if (arg == "--output-name") {
            set_spec_value(program.direct_spec, "output_name", require_string(args, i));
            program.has_direct_spec = true;
        } else if (arg == "--output-target" || arg == "-o") {
            set_spec_value(program.direct_spec, "output_target", require_string(args, i));
            program.has_direct_spec = true;
        } else if (arg == "--separator") {
            set_spec_value(program.direct_spec, "separator", require_string(args, i));
            program.has_direct_spec = true;
        } else if (arg == "--prefix") {
            set_spec_value(program.direct_spec, "prefix", require_string(args, i));
            program.has_direct_spec = true;
        } else if (arg == "--suffix") {
            set_spec_value(program.direct_spec, "suffix", require_string(args, i));
            program.has_direct_spec = true;
        } else {
            throw std::runtime_error("Unknown option: " + arg);
        }
    }

    return program;
}

void write_spec_output(Writer& out,
                       const Options& options,
                       const ObjectSpec& spec,
                       std::uint64_t total,
                       std::uint64_t count) {
    out << "# cppdb-configure-source v1\n";
    out << "# source_kind=configure_3\n";
    out << "# role=configured_object_fabric\n";
    out << "# record_mode=escaped_line\n";
    out << "# object_name=" << escape_field(spec.object_name) << '\n';
    out << "# input_name=" << escape_field(spec.input_name) << '\n';
    out << "# input_width=" << spec.input_width << '\n';
    out << "# input_size=" << spec.input_value.size() << '\n';
    out << "# flow_name=" << escape_field(spec.flow_name) << '\n';
    out << "# flow_type=" << flow_name(spec.flow) << '\n';
    out << "# output_name=" << escape_field(spec.output_name) << '\n';
    out << "# separator=" << escape_field(spec.separator) << '\n';
    out << "# prefix=" << escape_field(spec.prefix) << '\n';
    out << "# suffix=" << escape_field(spec.suffix) << '\n';
    out << "# expected_total=" << total << '\n';
    out << "# start=" << options.start << '\n';
    out << "# emitted_records=" << count << '\n';
    out << "# truncated=" << (options.start + count < total ? "true" : "false") << '\n';

    for (std::uint64_t i = 0; i < count; ++i) {
        const std::uint64_t ordinal = options.start + i;
        const std::string payload = make_payload(spec, ordinal);
        const std::string rendered = spec.prefix + payload + spec.suffix;

        out << "record\t"
            << ordinal << '\t'
            << escape_field(spec.object_name) << '\t'
            << escape_field(spec.input_name) << '\t'
            << escape_field(spec.flow_name) << '\t'
            << escape_field(spec.output_name) << '\t'
            << flow_name(spec.flow) << '\t'
            << payload.size() << '\t'
            << hex64(fnv1a_64(rendered)) << '\t'
            << escape_field(payload) << '\t'
            << escape_field(rendered) << '\n';
    }
}

void print_spec_plan(const Options& options,
                     const ObjectSpec& spec,
                     std::uint64_t total,
                     std::uint64_t count) {
    std::cout
        << "configure_3 dry run\n"
        << "  object: " << spec.object_name << '\n'
        << "  output: " << spec.output_target << '\n'
        << "  flow: " << flow_name(spec.flow) << '\n'
        << "  input width: " << spec.input_width << '\n'
        << "  expected total: " << total << '\n'
        << "  start: " << options.start << '\n'
        << "  records selected: " << count << '\n'
        << "  execute: false\n\n";
}

void emit_specs(const Options& options, const std::vector<ObjectSpec>& specs) {
    std::map<std::string, bool> initialized_targets;

    for (const ObjectSpec& spec : specs) {
        validate_spec(spec);
        const std::uint64_t total = expected_total(spec);
        const std::uint64_t count = requested_count(options, total);
        validate_safety(options, spec, count);

        if (!options.execute) {
            print_spec_plan(options, spec, total, count);
            continue;
        }

        if (spec.output_target == "-" || equals_ignore_case(spec.output_target, "stdout")) {
            StreamSink sink(std::cout);
            Writer writer(sink);
            write_spec_output(writer, options, spec, total, count);
            continue;
        }

        const bool already_initialized = initialized_targets[spec.output_target];
        if (!already_initialized && !options.overwrite && file_exists(spec.output_target)) {
            throw std::runtime_error("Output file already exists. Use --overwrite to replace it: " +
                                     spec.output_target);
        }

        FileSink sink(spec.output_target, already_initialized ? "ab" : "wb");
        Writer writer(sink);
        write_spec_output(writer, options, spec, total, count);
        initialized_targets[spec.output_target] = true;
        std::cout << "Wrote " << count << " configure_3 records for "
                  << spec.object_name << " to " << spec.output_target << '\n';
    }

    if (!options.execute) {
        std::cout << "Add --execute to write the escaped cppdb source-record output.\n";
    }
}

std::vector<ObjectSpec> resolve_specs(const Program& program, const char* executable) {
    if (!program.options.sample_config_path.empty()) {
        return {};
    }

    const bool uses_config = !program.options.config_path.empty();
    const bool uses_direct = program.has_direct_spec;

    if ((uses_config ? 1 : 0) + (program.options.menu ? 1 : 0) + (uses_direct ? 1 : 0) > 1) {
        throw std::runtime_error("Use only one input mode: --config, --menu, or direct object flags.");
    }

    if (uses_config) {
        return load_config(program.options.config_path);
    }

    if (program.options.menu) {
        return {prompt_spec()};
    }

    if (uses_direct) {
        validate_spec(program.direct_spec);
        return {program.direct_spec};
    }

    print_help(executable);
    return {};
}

int run(int argc, char** argv) {
    Program program = parse_program(argc, argv);

    if (!program.options.sample_config_path.empty()) {
        write_sample_config(program.options.sample_config_path, program.options.overwrite);
        std::cout << "Wrote sample config to " << program.options.sample_config_path << '\n';
        return 0;
    }

    const std::vector<ObjectSpec> specs = resolve_specs(program, argv[0]);
    if (specs.empty()) {
        return 0;
    }

    emit_specs(program.options, specs);
    return 0;
}

} // namespace

int main(int argc, char** argv) {
    try {
        return run(argc, argv);
    } catch (const std::exception& ex) {
        std::cerr << "configure_3 error: " << ex.what() << '\n';
        return 1;
    }
}
