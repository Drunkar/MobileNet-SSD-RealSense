import sys
graph_folder="./"
if sys.version_info.major < 3 or sys.version_info.minor < 4:
    print("Please using python3.4 or greater!")
    exit(1)

if len(sys.argv) > 1:
    graph_folder = sys.argv[1]

import pyrealsense2 as rs
import numpy as np
import cv2
from mvnc import mvncapi as mvnc
from os import system
import io, time
from os.path import isfile, join
import re
from time import sleep
import multiprocessing as mp

pipeline = None
lastresults = None
threads = []
processes = []
frameBuffer = None
results = None
fps = ""
framecount = 0
time1 = 0



def camThread(results, frameBuffer):
    global fps
    global lastresults
    global framecount
    global time1

    LABELS = ('background',
              'aeroplane', 'bicycle', 'bird', 'boat',
              'bottle', 'bus', 'car', 'cat', 'chair',
              'cow', 'diningtable', 'dog', 'horse',
              'motorbike', 'person', 'pottedplant',
              'sheep', 'sofa', 'train', 'tvmonitor')

    # Configure depth and color streams
    pipeline = rs.pipeline()
    config = rs.config()
    config.enable_stream(rs.stream.depth, 640, 480, rs.format.z16, 30)
    config.enable_stream(rs.stream.color, 640, 480, rs.format.bgr8, 30)
    pipeline.start(config)

    cv2.namedWindow('RealSense', cv2.WINDOW_AUTOSIZE)

    while True:
        t1 = time.perf_counter()

        # Wait for a coherent pair of frames: depth and color
        frames = pipeline.wait_for_frames()
        depth_frame = frames.get_depth_frame()
        color_frame = frames.get_color_frame()
        if not depth_frame or not color_frame:
            continue

        if frameBuffer.full():
            frameBuffer.get()

        color_image = np.asanyarray(color_frame.get_data())
        height = color_image.shape[0]
        width = color_image.shape[1]
        frameBuffer.put(color_image.copy())
        res = None

        if not results.empty():
            res = results.get(False)
            imdraw = overlay_on_image(frames, res, LABELS)
            lastresults = res
        else:
            imdraw = overlay_on_image(frames, lastresults, LABELS)

        cv2.imshow('RealSense', cv2.resize(imdraw, (width, height)))

        if cv2.waitKey(1)&0xFF == ord('q'):
            # Stop streaming
            if pipeline != None:
                pipeline.stop()
            sys.exit(0)

        ## Print FPS
        framecount += 1
        if framecount >= 15:
            fps = " {:.1f} FPS".format(time1/15)
            framecount = 0
            time1 = 0
        t2 = time.perf_counter()
        time1 += 1/(t2-t1)



def inferencer(results, frameBuffer):

    graph = None
    graphHandle0 = None
    graphHandle1 = None

    mvnc.global_set_option(mvnc.GlobalOption.RW_LOG_LEVEL, 4)
    devices = mvnc.enumerate_devices()
    if len(devices) == 0:
        print("No devices found")
        sys.exit(1)
    print(len(devices))

    with open(join(graph_folder, "graph"), mode="rb") as f:
        graph_buffer = f.read()
    graph = mvnc.Graph('MobileNet-SSD')

    devopen = False
    for devnum in range(len(devices)):
        try:
            device = mvnc.Device(devices[devnum])
            device.open()
            graphHandle0, graphHandle1 = graph.allocate_with_fifos(device, graph_buffer)
            devopen = True
            break
        except:
            continue

    if devopen == False:
        print("Devices open Error!!!")
        sys.exit(1)

    print("Loaded Graphs!!! "+str(devnum))

    while True:
        try:
            if frameBuffer.empty():
                continue

            color_image = frameBuffer.get()
            prepimg = preprocess_image(color_image)
            graph.queue_inference_with_fifo_elem(graphHandle0, graphHandle1, prepimg.astype(np.float32), color_image)
            out, _ = graphHandle1.read_elem()
            results.put(out)
        except:
            import traceback
            traceback.print_exc()



def preprocess_image(src):

    try:
        img = cv2.resize(src, (300, 300))
        img = img - 127.5
        img = img * 0.007843
        return img
    except:
        import traceback
        traceback.print_exc()      



def overlay_on_image(frames, object_info, LABELS):

    try:
        depth_frame = frames.get_depth_frame()
        color_frame = frames.get_color_frame()
        color_image = np.asanyarray(color_frame.get_data())

        if isinstance(object_info, type(None)):
            return color_image

        # Show images
        height = color_image.shape[0]
        width = color_image.shape[1]
        num_valid_boxes = int(object_info[0])
        img_cp = color_image.copy()

        if num_valid_boxes > 0:

            for box_index in range(num_valid_boxes):
                base_index = 7+ box_index * 7
                if (not np.isfinite(object_info[base_index]) or
                    not np.isfinite(object_info[base_index + 1]) or
                    not np.isfinite(object_info[base_index + 2]) or
                    not np.isfinite(object_info[base_index + 3]) or
                    not np.isfinite(object_info[base_index + 4]) or
                    not np.isfinite(object_info[base_index + 5]) or
                    not np.isfinite(object_info[base_index + 6])):
                    continue

                x1 = max(0, int(object_info[base_index + 3] * height))
                y1 = max(0, int(object_info[base_index + 4] * width))
                x2 = min(height, int(object_info[base_index + 5] * height))
                y2 = min(width, int(object_info[base_index + 6] * width))

                object_info_overlay = object_info[base_index:base_index + 7]

                min_score_percent = 60
                source_image_width = width
                source_image_height = height

                base_index = 0
                class_id = object_info_overlay[base_index + 1]
                percentage = int(object_info_overlay[base_index + 2] * 100)
                if (percentage <= min_score_percent):
                    continue

                box_left = int(object_info_overlay[base_index + 3] * source_image_width)
                box_top = int(object_info_overlay[base_index + 4] * source_image_height)
                box_right = int(object_info_overlay[base_index + 5] * source_image_width)
                box_bottom = int(object_info_overlay[base_index + 6] * source_image_height)
                meters = depth_frame.as_depth_frame().get_distance(box_left+int((box_right-box_left)/2), box_top+int((box_bottom-box_top)/2))
                label_text = LABELS[int(class_id)] + " (" + str(percentage) + "%)"+ " {:.2f}".format(meters) + " meters away"

                box_color = (255, 128, 0)
                box_thickness = 1
                cv2.rectangle(img_cp, (box_left, box_top), (box_right, box_bottom), box_color, box_thickness)

                label_background_color = (125, 175, 75)
                label_text_color = (255, 255, 255)

                label_size = cv2.getTextSize(label_text, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)[0]
                label_left = box_left
                label_top = box_top - label_size[1]
                if (label_top < 1):
                    label_top = 1
                label_right = label_left + label_size[0]
                label_bottom = label_top + label_size[1]
                cv2.rectangle(img_cp, (label_left - 1, label_top - 1), (label_right + 1, label_bottom + 1), label_background_color, -1)
                cv2.putText(img_cp, label_text, (label_left, label_bottom), cv2.FONT_HERSHEY_SIMPLEX, 0.5, label_text_color, 1)

        cv2.putText(img_cp, fps, (550,15), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (38,0,255), 1, cv2.LINE_AA)
        return img_cp

    except:
        import traceback
        traceback.print_exc()



if __name__ == '__main__':

    devices = None
    try:

        mp.set_start_method('forkserver')
        frameBuffer = mp.Queue(10)
        results = mp.Queue()

        # Start streaming
        p = mp.Process(target=camThread, args=(results, frameBuffer), daemon=True)
        p.start()
        processes.append(p)

        # Start detection MultiStick
        devices = mvnc.enumerate_devices()

        if len(devices) == 0:
            print("No devices found")
            quit()

        for devnum in range(len(devices)):
            p = mp.Process(target=inferencer, args=(results, frameBuffer), daemon=True)
            p.start()
            processes.append(p)

        while True:
            sleep(1)

    except:
        import traceback
        traceback.print_exc()
    finally:
        for p in range(len(processes)):
            processes[p].terminate()

        print("\n\nFinished\n\n")









