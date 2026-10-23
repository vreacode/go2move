// Diagnostic capture only: no hardware reset, firmware update or robot control.
// Run with the ROS RealSense driver stopped so both do not claim the same USB device.
#include <librealsense2/rs.hpp>
#include <nlohmann/json.hpp>
#include <chrono>
#include <cmath>
#include <fstream>
#include <iostream>
#include <mutex>
#include <memory>
#include <thread>
#include <vector>

using json = nlohmann::json;

struct Samples {
  std::mutex guard;
  json frames = json::object();
};

int main(int argc, char** argv)
{
  if (argc != 2 && !(argc == 3 && std::string(argv[2]) == "--simultaneous")) {
    std::cerr << "Usage: probe_depth_camera OUTPUT.json [--simultaneous]\n";
    return 2;
  }
  const bool simultaneous = argc == 3;
  json report = {{"robot_commands_sent", 0}, {"hardware_reset", false},
                 {"firmware_modified", false}, {"sdk", RS2_API_VERSION_STR},
                 {"capture_mode", simultaneous ? "simultaneous" : "individual"},
                 {"sensors", json::array()}};
  auto save = [&]() {
    std::ofstream output(argv[1]);
    output << report.dump(2) << '\n';
    if (!output) throw std::runtime_error("Cannot write diagnostic report");
  };
  try {
    rs2::context context;
    auto devices = context.query_devices();
    if (devices.size() != 1) {
      throw std::runtime_error("Expected exactly one RealSense device, found " +
                               std::to_string(devices.size()));
    }
    auto device = devices[0];
    report["device"] = {{"name", device.get_info(RS2_CAMERA_INFO_NAME)},
                        {"serial", device.get_info(RS2_CAMERA_INFO_SERIAL_NUMBER)},
                        {"firmware", device.get_info(RS2_CAMERA_INFO_FIRMWARE_VERSION)},
                        {"usb_type", device.get_info(RS2_CAMERA_INFO_USB_TYPE_DESCRIPTOR)},
                        {"product_id", device.get_info(RS2_CAMERA_INFO_PRODUCT_ID)}};
    if (std::string(device.get_info(RS2_CAMERA_INFO_NAME)).find("D435I") == std::string::npos) {
      throw std::runtime_error("Connected device is not the confirmed D435I");
    }
    save();
    auto sensors = device.query_sensors();
    std::vector<std::vector<rs2::stream_profile>> profiles;
    std::vector<std::shared_ptr<Samples>> samples;
    std::vector<bool> opened(sensors.size(), false), started(sensors.size(), false);
    for (auto sensor : sensors) {
      const std::string name = sensor.get_info(RS2_CAMERA_INFO_NAME);
      const auto index = report["sensors"].size();
      report["sensors"].push_back({{"name", name}, {"selected_profiles", json::array()},
                                  {"frames", json::object()}, {"started", false}});
      std::vector<rs2::stream_profile> selected;
      for (auto stream : {RS2_STREAM_DEPTH, RS2_STREAM_COLOR, RS2_STREAM_ACCEL, RS2_STREAM_GYRO}) {
        for (auto profile : sensor.get_stream_profiles()) {
          if (profile.stream_type() != stream) continue;
          auto video = profile.as<rs2::video_stream_profile>();
          bool matches = false;
          if (stream == RS2_STREAM_DEPTH || stream == RS2_STREAM_COLOR) {
            matches = video && video.width() == 640 && video.height() == 480 &&
                      profile.fps() == 15 &&
                      profile.format() == (stream == RS2_STREAM_DEPTH ? RS2_FORMAT_Z16 : RS2_FORMAT_RGB8);
          } else {
            matches = profile.format() == RS2_FORMAT_MOTION_XYZ32F &&
                      profile.fps() == (stream == RS2_STREAM_ACCEL ? 100 : 200);
          }
          if (!matches) continue;
          selected.push_back(profile);
          report["sensors"][index]["selected_profiles"].push_back({
            {"stream", rs2_stream_to_string(stream)}, {"fps", profile.fps()},
            {"format", rs2_format_to_string(profile.format())}});
          break;
        }
      }
      profiles.push_back(selected);
      samples.push_back(std::make_shared<Samples>());
    }
    auto finish = [&](std::size_t index) {
      if (started[index]) {
        try { sensors[index].stop(); }
        catch (const std::exception& error) { report["sensors"][index]["stop_error"] = error.what(); }
      }
      if (opened[index]) {
        try { sensors[index].close(); }
        catch (const std::exception& error) { report["sensors"][index]["close_error"] = error.what(); }
      }
      {
        std::lock_guard<std::mutex> lock(samples[index]->guard);
        report["sensors"][index]["frames"] = samples[index]->frames;
      }
      save();
      std::cout << report["sensors"][index].dump() << std::endl;
    };
    for (std::size_t index = 0; index < sensors.size(); ++index) {
      if (profiles[index].empty()) {
        report["sensors"][index]["error"] = "No expected diagnostic profile supported";
        continue;
      }
      try {
        sensors[index].open(profiles[index]);
        opened[index] = true;
        auto stats = samples[index];
        sensors[index].start([stats](rs2::frame frame) {
          const std::string stream = rs2_stream_to_string(frame.get_profile().stream_type());
          std::lock_guard<std::mutex> lock(stats->guard);
          auto& entry = stats->frames[stream];
          if (entry.is_null()) entry = {{"count", 0}};
          entry["count"] = entry["count"].get<int>() + 1;
          entry["last_timestamp_ms"] = frame.get_timestamp();
          if (auto motion = frame.as<rs2::motion_frame>()) {
            auto vector = motion.get_motion_data();
            entry["last_xyz"] = {vector.x, vector.y, vector.z};
            entry["finite"] = std::isfinite(vector.x) && std::isfinite(vector.y) && std::isfinite(vector.z);
          }
        });
        started[index] = true;
        report["sensors"][index]["started"] = true;
        save();
        if (!simultaneous) std::this_thread::sleep_for(std::chrono::seconds(8));
      } catch (const std::exception& error) {
        report["sensors"][index]["error"] = error.what();
      }
      if (!simultaneous) finish(index);
    }
    if (simultaneous) {
      std::this_thread::sleep_for(std::chrono::seconds(8));
      for (std::size_t index = 0; index < sensors.size(); ++index) finish(index);
    }
    report["completed"] = true;
    save();
    std::cout << report["device"].dump() << std::endl;
    return 0;
  } catch (const std::exception& error) {
    report["completed"] = false;
    report["error"] = error.what();
    save();
    std::cerr << error.what() << std::endl;
    return 1;
  }
}
