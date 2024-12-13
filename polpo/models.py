import abc

import numpy as np
from sklearn.decomposition import PCA
from sklearn.linear_model import LinearRegression
from sklearn.pipeline import Pipeline as SklearnPipeline
from sklearn.preprocessing import FunctionTransformer, StandardScaler

from .preprocessing import IdentityStep, ListSqueeze
from .preprocessing.sklearn.adapter import AdapterPipeline
from .preprocessing.sklearn.base import GetParamsMixin
from .preprocessing.sklearn.compose import ObjectBasedTransformedTargetRegressor
from .preprocessing.sklearn.mesh import InvertibleMeshesToVertices
from .preprocessing.sklearn.np import InvertibleFlattenButFirst
from .preprocessing.sklearn.point_cloud import (
    FittableRegisteredPointCloudSmoothing,
)


class Model(abc.ABC):
    @abc.abstractmethod
    def predict(self, X):
        pass


class ModelFactory(abc.ABC):
    @abc.abstractmethod
    def create(self):
        # returns a `Model`
        pass


class ConstantOutput(Model):
    # for debugging
    def __init__(self, value):
        super().__init__()
        self.value = value

    def predict(self, X=None):
        return self.value


class ListLookup(Model):
    def __init__(self, data, tar=0):
        super().__init__()
        self.data = data
        self.tar = tar

    def predict(self, X):
        # NB: expects a (int,)
        return self.data[X[0] - self.tar]


class PdDfLookup(Model):
    def __init__(self, df, output_keys, tar=0):
        super().__init__()
        self.df = df
        self.output_keys = output_keys
        self.tar = tar

    def predict(self, X):
        # NB: expects a (int,)

        # TODO: make it input key based instead?
        df_session = self.df.iloc[X[0] - self.tar]
        df_session.columns = self.df.columns

        return [df_session.get(key) for key in self.output_keys]


class MriSlicesLookup(Model):
    def __init__(self, data, index_tar=1, index_ordering=(0, 1, 2)):
        self.data = data
        self.index_tar = index_tar
        self.index_ordering = index_ordering

    def predict(self, X):
        index, *slice_indices = X

        datum = self.data[index - self.index_tar]

        slices = []
        for index, slice_index in zip(self.index_ordering, slice_indices):
            slicing_indices = [slice(None)] * 3
            slicing_indices[index] = slice_index
            slices.append(datum[tuple(slicing_indices)])

        common_width = max([len(slice_[:, 0]) for slice_ in slices])
        common_height = max([len(slice_[0]) for slice_ in slices])

        for i_slice, slice_ in enumerate(slices):
            if len(slice_[:, 0]) < common_width:
                diff = common_width - len(slice_[:, 0])
                slice_ = np.pad(
                    slice_, ((diff // 2, diff // 2), (0, 0)), mode="constant"
                )
                slices[i_slice] = slice_
            if len(slice_[0]) < common_height:
                diff = common_height - len(slice_[0])
                slice_ = np.pad(
                    slice_, ((0, 0), (diff // 2, diff // 2)), mode="constant"
                )
                slices[i_slice] = slice_

        return slices


class ObjectRegressor(GetParamsMixin, SklearnPipeline):
    # just syntax sugar

    def __init__(self, model, x2x=None, objs2y=None, x_scaler=None):
        # TODO: add warning?
        # x_scaler is ignored if x2x is not None

        if model is None:
            model = LinearRegression()

        if x2x is None:
            steps = [np.array, np.atleast_2d]
            if x_scaler is not None:
                steps.append(x_scaler)
            x2x = AdapterPipeline(steps=steps)

        tmodel = ObjectBasedTransformedTargetRegressor(
            regressor=model,
            transformer=objs2y,
            check_inverse=False,
        )

        super().__init__(
            steps=[
                ("preprocessing", x2x),
                ("model", tmodel),
            ]
        )


class VertexBasedMeshRegressor(ObjectRegressor):
    # just syntax sugar

    def __init__(
        self,
        model=None,
        x2x=None,
        meshes2vertices=None,
        x_scaler=None,
        y_smoother=None,
    ):
        if y_smoother is None:
            y_smoother = FittableRegisteredPointCloudSmoothing(n_neighbors=10)

        if meshes2vertices is None:
            meshes2vertices = AdapterPipeline(
                steps=[
                    FunctionTransformer(func=np.squeeze),  # undo sklearn 2d
                    FunctionTransformer(inverse_func=ListSqueeze(raise_=False)),
                    InvertibleMeshesToVertices(),
                    y_smoother,
                    FunctionTransformer(func=np.stack),
                    InvertibleFlattenButFirst(),
                ],
            )

        super().__init__(model, x2x=x2x, objs2y=meshes2vertices, x_scaler=x_scaler)


class DimReductionBasedMeshRegressor(ObjectRegressor):
    # just syntax sugar

    def __init__(
        self,
        model=None,
        x2x=None,
        meshes2components=None,
        y_scaler=None,
        y_smoother=None,
        dim_reduction=None,
        x_scaler=None,
    ):
        # TODO: add warning?
        # y_scaler is ignored if mesh2components is not None
        # dim_reduction is ignored if mesh2components is not None

        if y_scaler is None:
            # TODO: allow False
            y_scaler = StandardScaler(with_std=False)

        if dim_reduction is None:
            # TODO: allow False
            dim_reduction = PCA()

        if y_smoother is None:
            # TODO: create identity; allow False
            y_smoother = FittableRegisteredPointCloudSmoothing(n_neighbors=10)

        if meshes2components is None:
            meshes2components = AdapterPipeline(
                steps=[
                    FunctionTransformer(func=np.squeeze),  # undo sklearn 2d
                    FunctionTransformer(inverse_func=ListSqueeze(raise_=False)),
                    InvertibleMeshesToVertices(),
                    FunctionTransformer(func=np.stack),
                    y_smoother,
                    InvertibleFlattenButFirst(),
                    y_scaler,
                    dim_reduction,
                ],
            )

        super().__init__(model, x2x=x2x, objs2y=meshes2components, x_scaler=x_scaler)


class SklearnLikeModelFactory(ModelFactory):
    # TODO: inherit from some pipeline based factory?

    def __init__(self, model, data, pipeline=None):
        self.model = model
        self.data = data

        if pipeline is None:
            pipeline = IdentityStep()

        self.pipeline = pipeline

    def create(self):
        X, y = self.pipeline(self.data)
        return self.model.fit(X=X, y=y)
