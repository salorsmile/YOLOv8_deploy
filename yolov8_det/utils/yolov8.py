import cv2
import onnxruntime
import time
import numpy as np
import base64
import requests
from io import BytesIO
from PIL import Image

import error_type.error_common as error_common
from yolov8_det.utils.compute import multiclass_nms, xywh2xyxy
from values.strings import legal_url_v1


class YOLOv8:
    def __init__(self, model_path, img_size=(640, 640), conf_thres=0.25, iou_thres=0.7):
        self.input_height = img_size[0]
        self.input_width = img_size[1]
        self.conf_threshold = conf_thres
        self.iou_threshold = iou_thres
        self.initialize_model(model_path)       # Initialize model

    def __call__(self, image):
        return self.detect_objects(image)

    def initialize_model(self, model_path):
        self.session = onnxruntime.InferenceSession(model_path, providers=onnxruntime.get_available_providers())
        self.get_model_details()

    def detect_objects(self, image):
        t0 = time.perf_counter()    # start time
        try:
            input_tensor = self.prepare_input(image)

        except (error_common.ParsingUrlError, error_common.ReadImageError,
                error_common.InvalidImageError, error_common.InputFormatError) as e:
            raise error_common.PreProcessError(f"{e}: Read image failed.")

        except Exception as e:
            raise error_common.PreProcessError(f"{e}: Get input tensor failed.")

        t1 = time.perf_counter()    # preprocess time

        try:
            outputs = self.inference(input_tensor)

        except Exception as e:
            raise error_common.DetectionInferError(f"{e}: Detection model infer failed.")

        t2 = time.perf_counter()    # model infer time

        try:
            self.boxes, self.scores, self.class_ids = self.process_output(outputs)

        except Exception as e:
            raise error_common.PostProcessError(f"{e}: Post-process failed.")

        t3 = time.perf_counter()    # total time cost, and postprocess time

        print(f"Total time: {(t3 - t0) * 1000:.2f} ms, Preprocess time: {(t1 - t0) * 1000:.2f} ms, "
              f"Inference time: {(t2 - t1) * 1000:.2f} ms, Postprocess time: {(t3 - t2) * 1000:.2f} ms.")

        return self.boxes, self.scores, self.class_ids, (t3 - t0, t1 - t0, t2 - t1, t3 - t2)

    def prepare_input(self, image_input):
        image = self.get_image(image_input)

        self.img_height, self.img_width = image.shape[:2]
        input_img = cv2.resize(image, (self.input_width, self.input_height))
        # input_img = cv2.cvtColor(input_img, cv2.COLOR_BGR2RGB)    # Convert BGR to RGB

        # Scale input pixel values to 0 to 1 and transpose
        input_img = (input_img / 255.0).astype(np.float32)
        input_tensor = np.transpose(input_img, (2, 0, 1))[np.newaxis, :, :, :]

        return input_tensor

    def inference(self, input_tensor):
        return self.session.run(self.output_names, {self.input_names[0]: input_tensor})

    def process_output(self, output):
        predictions = np.squeeze(output[0]).T

        # Filter out object confidence scores below threshold
        scores = np.max(predictions[:, 4:], axis=1)
        predictions = predictions[scores > self.conf_threshold, :]
        scores = scores[scores > self.conf_threshold]

        if len(scores) == 0:
            return (np.array([], dtype=np.float32).reshape(0, 4), np.array([], dtype=np.int32),
                    np.array([], dtype=np.float32))

        # Get the class with the highest confidence
        class_ids = np.argmax(predictions[:, 4:], axis=1)

        # Get bounding boxes for each object
        boxes = self.extract_boxes(predictions)

        # Apply non-maxima suppression to suppress weak, overlapping bounding boxes
        # indices = nms(boxes, scores, self.iou_threshold)
        indices = multiclass_nms(boxes, scores, class_ids, self.iou_threshold)

        return boxes[indices], scores[indices], class_ids[indices]

    def extract_boxes(self, predictions):
        boxes = predictions[:, :4]          # Extract boxes from predictions
        boxes = self.rescale_boxes(boxes)   # Scale boxes to original image dimensions
        boxes = xywh2xyxy(boxes)            # Convert boxes to xyxy format

        return boxes

    def rescale_boxes(self, boxes):
        # Rescale boxes to original image dimensions
        input_shape = np.array([self.input_width, self.input_height, self.input_width, self.input_height])
        boxes = np.divide(boxes, input_shape, dtype=np.float32)
        boxes *= np.array([self.img_width, self.img_height, self.img_width, self.img_height])

        return boxes

    def get_model_details(self):
        model_inputs = self.session.get_inputs()
        model_outputs = self.session.get_outputs()

        self.input_names = [model_inputs[i].name for i in range(len(model_inputs))]
        self.output_names = [model_outputs[i].name for i in range(len(model_outputs))]

    @staticmethod
    def get_image(image_input):
        read_type = "url"

        if isinstance(image_input, str):
            if legal_url_v1.match(image_input):
                try:
                    response = requests.get(image_input)
                    img_rgb = np.array(Image.open(BytesIO(response.content)).convert('RGB'))

                except Exception as e:
                    raise error_common.ParsingUrlError(f"{e}: Unable to parse URL.")
            else:
                try:
                    img_data = base64.b64decode(image_input)
                    img_rgb = np.array(Image.open(BytesIO(img_data)).convert('RGB'))
                    read_type = "base64"

                except Exception:
                    try:
                        img = cv2.imread(image_input)
                        img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                        read_type = "image path"

                    except Exception as e:
                        raise error_common.ReadImageError(f"{e}: Read image failed, get invalid input str.")

        elif isinstance(image_input, np.ndarray):
            img_rgb = image_input
        else:
            raise error_common.InputFormatError(f"Error: Get invalid input format {type(image_input)}")

        if img_rgb is None or len(img_rgb.shape) != 3:
            raise error_common.InvalidImageError(f"Error: Get invalid image (None or dim != 3), read by [{read_type}]")

        return img_rgb


if __name__ == '__main__':
    from yolov8_det.utils.image import draw_detections_pipeline, img_to_base64

    # 加载模型
    model_path = "../../models/det_hand/hands_07_15_m.onnx"
    yolov8_detector = YOLOv8(model_path, conf_thres=0.3, iou_thres=0.5)

    # 加载图片
    img_1 = "https://pic4.zhimg.com/80/v2-81b33cc28e4ba869b7c2790366708e97_1440w.webp"  # URL读取
    img_2 = "../../test_data/paml_1.jpg"    # base64读取
    # _, buffer = cv2.imencode(".jpg", cv2.imread(img_2))
    # img_2 = base64.b64encode(buffer).decode('utf-8')
    img_2 = img_to_base64(cv2.imread(img_2), rgb=False)
    img_3 = "../../test_data/zidane.jpg"    # 路径读取
    img_4 = cv2.imread(img_3)   # np数组读取
    img_4 = cv2.cvtColor(img_4, cv2.COLOR_BGR2RGB)

    # 推理并绘制图片
    for img in [img_1, img_2, img_3, img_4]:
        img_rgb = yolov8_detector.get_image(img)
        img_bgr = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2BGR)

        cv2.namedWindow("Output", cv2.WINDOW_NORMAL)
        cv2.imshow("Output", img_bgr)
        cv2.waitKey(0)

        bbox, conf, cls, _ = yolov8_detector(img_rgb)
        print(bbox, conf, cls)

        img_plot = draw_detections_pipeline(img_bgr, bbox, conf, cls)

        cv2.namedWindow("Output", cv2.WINDOW_NORMAL)
        cv2.imshow("Output", img_plot)
        cv2.waitKey(0)
