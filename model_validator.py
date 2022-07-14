"""
Benchmark via comparing output tensors

Parameters
----------
"""
from __future__ import print_function
from logging import raiseExceptions

import sys
import os
import subprocess, shlex
import re
import argparse
import numpy as np
import tensorflow as tf


GLOBAL_SETTING = {
    'iteration' : 1,
    'rtol' : 1e-5,
    'atol' : 1e-5,
    'target' : 'heaan',
}


def convert_to_list(x):
    if not isinstance(x, list):
        x = [x]
    return x


def run_tflite_on_host(tflite_file, inputs):
    with open(tflite_file, 'rb') as f:
        model_buf = f.read()
    inputs = convert_to_list(inputs)

    runtime = tf.lite.Interpreter(model_content=model_buf)
    input_details = runtime.get_input_details()
    output_details = runtime.get_output_details()

    for i, input_detail in enumerate(input_details):
        runtime.resize_tensor_input(input_detail['index'], inputs[i].shape)
    runtime.allocate_tensors()

    assert len(inputs) == len(input_details)

    for i, input_detail in enumerate(input_details):
        runtime.set_tensor(input_detail['index'], inputs[i])
    runtime.invoke()

    print(runtime.get_tensor(input_details[i]['index']))

    outputs = []
    for _, output_detail in enumerate(output_details):
        shape = output_detail['shape']
        outputs.append(np.reshape(runtime.get_tensor(output_detail['index']), shape))
    return outputs


def run_tflite_on_heaan(tflite_file, inputs, use_target='--use_npu=true'):
    with open(tflite_file, 'rb') as f:
        model_buf = f.read()
    inputs = convert_to_list(inputs)

    runtime = tf.lite.Interpreter(model_content=model_buf)
    input_details = runtime.get_input_details()
    output_details = runtime.get_output_details()

    # Do something.

    outputs = []
    for _, output_detail in enumerate(output_details):
        shape = output_detail['shape']
        outputs.append(np.reshape(runtime.get_tensor(output_detail['index']), shape))
    return outputs


def run_tflite_on_android(tflite_file, inputs, use_target='--use_npu=true'):
    with open(tflite_file, 'rb') as f:
        model_buf = f.read()
    inputs = convert_to_list(inputs)

    runtime = tf.lite.Interpreter(model_content=model_buf)
    input_details = runtime.get_input_details()
    output_details = runtime.get_output_details()

    root = '/data/local/tmp'
    input_layer = ''
    input_layer_shape = ''
    tails = ''
    try:
        r = subprocess.check_call(f'adb push {tflite_file} {root}', shell=True)
    except Exception as exc:
        raise RuntimeError(f'Failed to push tflite model {tflite_file}') from exc
    for i, input_detail in enumerate(input_details):
        name = input_detail['name']
        shape = input_detail['shape'].tolist()
        name = name.replace(':','_')
        shape = ','.join([str(_) for _ in shape])
        with open(f'.invals{i}', 'w').encoding('UTF8') as f:
            np.array(inputs[i]).tofile(f)
        try:
            r = subprocess.check_call(f'adb push .invals{i} {root}', shell=True)
        except Exception as exc:
            raise RuntimeError(f'Failed to push input tensor file .invals{i}') from exc
        tails += f'{name}:{root}/.invals{i},'
        input_layer += f'{name},'
        input_layer_shape += f'{shape}:'

    input_layer = input_layer.rstrip(',')
    input_layer_shape = input_layer_shape.rstrip(':')
    tails = tails.rstrip(',')
    split_file_path = tflite_file.split('/')
    file_name = split_file_path[-1]

    command = f'adb shell {root}/benchmark_model --graph={root}/{file_name} \
        --num_runs=1 --min_secs=0 --max_secs=0 {use_target} --input_layer={input_layer} \
        --input_layer_shape={input_layer_shape} --input_layer_value_files={tails} \
        --save_outputs_in_file={root}/.outvals'

    try:
        subprocess.check_output(
            shlex.split('adb root'), stderr=subprocess.STDOUT
        ).decode('utf-8')
        subprocess.check_call(command, shell=True)
        subprocess.check_call(f'adb pull {root}/.outvals .', shell=True)
    except Exception as exc:
        raise RuntimeError(f'Failed to run {root}/benchmark_model') from exc

    outputs = []
    for i, output_detail in enumerate(output_details):
        shape = output_detail['shape']
        for _, shape_ in enumerate(shape):
            read_size *= int(shape_)

        with open('.outvals', 'rb') as f:
            f.seek(read_size * i)
            outbuf = f.read(read_size)

        outputs.append(np.reshape(np.frombuffer(outbuf, output_detail['dtype'], shape)))
    return outputs


def probe_adb_device():
    try:
        log = subprocess.check_output(
                shlex.split('adb devices'),
                stderr=subprocess.STDOUT).decode('utf-8')
    except Exception as exc:
        raise RuntimeError('Check first if adb works well in your host.') from exc
    rex = re.compile('(?P<dev_id>[A-Z|0-9]+)\s+[a-z]+', re.DOTALL)
    dev_lists = list(rex.finditer(log))
    if len(dev_lists) > 1 and not os.getenv('ANDROID_SERIAL'):
        raise RuntimeError(
                'Multiple devices seem to be connected to your host, \
                 which is not desirable for this test scheme. \
                 Remain only one device connected, then try again.')
    if len(dev_lists) == 1 and log.find('unauthorized') != -1:
        raise RuntimeError(
                'Make sure if \'USB debugging\' in \'developer options\' \
                is allowed first, then try again.')
    if len(dev_lists) == 0:
        raise RuntimeError('Make sure if your device is configured to enable \
                \'developer options\' first, then try again.')
    return dev_lists[0].group('dev_id')


def compare_output(from_host, from_target, metric='Strict', k=0):
    """Version of np.testing.assert_allclose with `atol` and `rtol` fields set
    in reasonable defaults.

    Arguments `from_host` and `from_target` are not interchangeable, since the function
    compares the `abs(actual-desired)` with `atol+rtol*abs(desired)`.  Since we
    often allow `desired` to be close to zero, we generally want non-zero `atol`.
    """
    for i in enumerate(from_host):
        o_host = np.asanyarray(from_host[i])
        o_targ = np.asanyarray(from_target[i])

        assert o_host.shape == o_targ.shape

        if metric is None or len(o_host.shape) > 2 :
            metric = 'Strict'

        if metric == 'Strict':
            np.testing.assert_allclose(
                o_host, o_targ,
                rtol=GLOBAL_SETTING['rtol'],
                atol=GLOBAL_SETTING['atol'],
                verbose=True
            )
        elif metric == 'TopK':
            assert o_host.size > k

            if len(o_host.shape) == 1:
                topk_host = (-o_host).argsort()[:k]
                topk_targ = (-o_targ).argsort()[:k]
            else:
                topk_host = (-o_host).argsort()[:,:k]
                topk_targ = (-o_targ).argsort()[:,:k]
            np.testing.assert_almost_equal(np.sort(topk_host), np.sort(topk_targ))


class model_validator:
    def __init__(self, f):
        self.inner_f = f
        if GLOBAL_SETTING['target'] == 'android':
            self.target_id = probe_adb_device()

    def __call__(self, *args, **kwargs):
        tflite_file, inputs = self.inner_f(self)

        x = run_tflite_on_host(tflite_file, inputs)
        if GLOBAL_SETTING['target'] == 'heaan':
            y = run_tflite_on_heaan(tflite_file, inputs)
        elif GLOBAL_SETTING['target'] == 'andriod':
            y = run_tflite_on_android(tflite_file, inputs)
        else:
            raise Exception('Not yet supported')

        for _ in range(GLOBAL_SETTING['iteration']):
            compare_output(
                x,
                y,
                kwargs.get('metric'),
                kwargs.get('k'),
            )
        print("###### test result : Pass #####")


"""
Add test cases each per a single tflite model here with @model_validator
"""
class TestRecipes:
    def __init__(self):
        return

    @model_validator
    def do_dummy(self):
        # Responsible to return the **tuple** in that
        # - **tflite file path**
        # - **inputs** composed of numpy arrays for the given operation.
        # must be given by the author.
        input_a = np.random.uniform(size=(2, 2)).astype('float32')
        input_b = np.random.uniform(size=(2, 2)).astype('float32')
        # inputs can be both a list of numpy objects or a single numpy object.
        return os.path.join(os.path.dirname(__file__), 'models/', 'add_fp16.tflite'), [input_a, input_b]

    @model_validator
    def do_mul(self):
        input_a = np.random.uniform(size=(2, 2), low=0, high=10).astype('int8')
        input_b = np.random.uniform(size=(2, 2), low=0, high=10).astype('int8')
        return os.path.join(os.path.dirname(__file__), 'models/', 'mul_int8.tflite'), [input_a, input_b]

    @model_validator
    def do_argmax(self):
        inputs = np.random.uniform(size=(1, 720, 1080, 3)).astype('float32')
        return os.path.join(os.path.dirname(__file__), 'models/', 'argmax.tflite'), inputs

    @model_validator
    def do_relu(self):
        inputs = np.random.uniform(size=(32), low=-1., high=1.).astype('float32')
        return os.path.join(os.path.dirname(__file__), 'models/', 'relu.tflite'), inputs

    @model_validator
    def do_relu6(self):
        inputs = np.random.uniform(size=(32), low=-1., high=1.).astype('float32')
        return os.path.join(os.path.dirname(__file__), 'models/', 'relu6.tflite'), inputs

    @model_validator
    def do_elu(self):
        inputs = np.random.uniform(size=(32), low=-1., high=1.).astype('float32')
        return os.path.join(os.path.dirname(__file__), 'models/', 'elu.tflite'), inputs

    @model_validator
    def do_prelu(self):
        inputs = np.random.uniform(size=(32), low=-1., high=1.).astype('float32')
        return os.path.join(os.path.dirname(__file__), 'models/', 'prelu.tflite'), inputs

    @model_validator
    def do_tanh(self):
        inputs = np.random.uniform(size=(32), low=-1., high=1.).astype('float32')
        return os.path.join(os.path.dirname(__file__), 'models/', 'tanh.tflite'), inputs

    @model_validator
    def do_dense(self):
        inputs = np.random.uniform(size=(32), low=-3., high=3.).astype('float32')
        return os.path.join(os.path.dirname(__file__), 'models/', 'dense.tflite'), inputs

    @model_validator
    def do_depthwiseconv2d(self):
        inputs = np.random.uniform(size=(1, 32, 32, 32), low=-3., high=3.).astype('float32')
        return os.path.join(os.path.dirname(__file__), 'models/', 'depthwise_conv2d.tflite'), inputs

    @model_validator
    def do_transpose(self):
        inputs = np.random.uniform(size=(3, 2), low=-10., high=10.).astype('float32')
        return os.path.join(os.path.dirname(__file__), 'models/', 'transpose.tflite'), inputs

    @model_validator
    def do_conv2d(self):
        inputs = np.random.uniform(size=(1, 512, 512, 3), low=0., high=16.).astype('float32')
        return os.path.join(os.path.dirname(__file__), 'models/', 'conv2d.tflite'), inputs

    @model_validator
    def do_conv2d_int8(self):
        inputs = np.random.uniform(size=(1, 512, 512, 3), low=0, high=16).astype('int8')
        return os.path.join(os.path.dirname(__file__), 'models/', 'conv2d_int8.tflite'), inputs

    @model_validator
    def do_maxpool2d(self):
        inputs = np.random.uniform(size=(1, 32, 32, 1), low=0., high=16.).astype('float32')
        return os.path.join(os.path.dirname(__file__), 'models/', 'maxpool2d.tflite'), inputs

    @model_validator
    def do_reshape(self):
        inputs = np.random.uniform(size=(2, 720, 1080), low=-10., high=10.).astype('float32')
        return os.path.join(os.path.dirname(__file__), 'models/', 'reshape_2X720X1080.tflite'), inputs

    @model_validator
    def do_pad(self):
        inputs = np.random.uniform(size=(1, 3, 3, 1), low=0., high=5.).astype('float32')
        return os.path.join(os.path.dirname(__file__), 'models/', 'pad.tflite'), inputs

    @model_validator
    def do_densenet(self):
        inputs = np.random.uniform(size=(1, 224, 224, 3), low=0., high=255.).astype('float32')
        return os.path.join(os.path.dirname(__file__), 'models/', 'densenet.tflite'), inputs

    @model_validator
    def do_inception(self):
        inputs = np.random.uniform(size=(1, 299, 299, 3), low=0., high=255.).astype('float32')
        return os.path.join(os.path.dirname(__file__), 'models/', 'inception_v3.tflite'), inputs

    @model_validator
    def do_mobilenet(self):
        inputs = np.random.uniform(size=(1, 224, 224, 3), low=0., high=255.).astype('float32')
        return os.path.join(os.path.dirname(__file__), 'models/', 'mobilenet_v2_1.0_224.tflite'), inputs

    @model_validator
    def do_split(self):
        inputs = np.random.uniform(size=(1, 720, 4, 3), low=-128., high=127.).astype('float32')
        return os.path.join(os.path.dirname(__file__), 'models/', 'split_fp32.tflite'), inputs

    @model_validator
    def do_split_int8(self):
        inputs = np.random.uniform(size=(1, 720, 4, 3), low=-128., high=127.).astype('int8')
        return os.path.join(os.path.dirname(__file__), 'models/', 'split_int8.tflite'), inputs

    @model_validator
    def do_mobilebert(self):
        input1 = np.random.uniform(size=(1, 384), low=0, high=255).astype('int32')
        input2 = np.random.uniform(size=(1, 384), low=0, high=255).astype('int32')
        input3 = np.random.uniform(size=(1, 384), low=0, high=255).astype('int32')
        return os.path.join(os.path.dirname(__file__), 'models/', 'mobilebert_1_default_1.tflite'), [input1, input2, input3]

    @model_validator
    def do_unpack_int8(self):
        inputs = np.random.uniform(size=(3, 4), low=0., high=100.).astype('int8')
        return os.path.join(os.path.dirname(__file__), 'models/', 'unpack_int8_quant.tflite'), inputs

    @model_validator
    def do_unpack(self):
        inputs = np.random.uniform(size=(3, 4), low=0., high=100.).astype('float32')
        return os.path.join(os.path.dirname(__file__), 'models/', 'unpack.tflite'), inputs

    @model_validator
    def do_cast(self):
        inputs = np.random.uniform(size=(2, 3, 4), low=0., high=100.).astype('int8')
        return os.path.join(os.path.dirname(__file__), 'models/', 'cast_int8_quant.tflite'), inputs

    @model_validator
    def do_stridedslice(self):
        inputs = np.random.uniform(size=(3, 2, 3), low=0., high=100.).astype('float32')
        return os.path.join(os.path.dirname(__file__), 'models/', 'strided_slice.tflite'), inputs


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--model', action='store',
        choices=[
            'all',
            'stridedslice',
            'cast',
            'split',
            'split_int8',
            'split',
            'pad',
            'mul',
            'conv2d',
            'maxpool2d',
            'reshape',
            'depthwiseconv2d',
            'conv2d_int8',
            'dense',
            'argmax',
            'relu',
            'relu6',
            'prelu6',
            'tanh',
            'densenet',
            'inception',
            'mobilenet',
            'mobilebert',
            'unpack',
            'unpack_int8'],
        default='all', help="test model"
    )
    parser.add_argument(
        '--target', action='store',
        choices = [
            'heaan',
            'android',
            'ios',
            'raspberrypi'],
        default='heaan', help='target specified for benchmark'
    )
    args = parser.parse_args()
    return args


test_func = {
    'split' : (lambda : TestRecipes().do_split()),
    'split_int8' : (lambda : TestRecipes().do_split_int8()),
    'pad' : (lambda : TestRecipes().do_pad()),
    'conv2d' : (lambda : TestRecipes().do_conv2d()),
    'mul' : (lambda : TestRecipes().do_mul()),
    'maxpool2d' : (lambda : TestRecipes().do_maxpool2d()),
    'reshape' : (lambda : TestRecipes().do_reshape()),
    'transpose' : (lambda : TestRecipes().do_transpose()),
    'depthwiseconv2d' : (lambda : TestRecipes().do_depthwiseconv2d()),
    'dense' : (lambda : TestRecipes().do_dense()),
    'argmax' : (lambda : TestRecipes().do_argmax()),
    'relu' : (lambda : TestRecipes().do_relu()),
    'relu6' : (lambda : TestRecipes().do_relu6()),
    'prelu' : (lambda : TestRecipes().do_prelu()),
    'tanh' : (lambda : TestRecipes().do_tanh()),
    'densenet' : (lambda : TestRecipes().do_densenet()),
    'inception' : (lambda : TestRecipes().do_inception()),
    'mobilenet' : (lambda : TestRecipes().do_mobilenet(metric='TopK', k=5)),
    'conv2d_int8' : (lambda : TestRecipes().do_conv2d_int8()),
    'mobilebert' : (lambda : TestRecipes().do_mobilebert(metric='TopK', k=5)),
    'unpack_int8' : (lambda : TestRecipes().do_unpack_int8()),
    'unpack' : (lambda : TestRecipes().do_unpack()),
    'cast' : (lambda : TestRecipes().do_cast()),
    'stridedslice' : (lambda : TestRecipes().do_stridedslice()),
}


def main(args):
    model  = args.model
    GLOBAL_SETTING['target'] = args.target
    if model == 'all':
        for func in test_func.values():
            func()
    else:
        test_func[model]()


if __name__ == "__main__":
    main(parse_args())
    